"""Typed access to the command-module MCU's own ProgCom register area."""

from enum import IntEnum, IntFlag
import struct
from typing import Tuple

from ..compat import dataclass
from .base import Device


MCU_MAGIC = b"CMCU"
MCU_MAP_MAJOR = 1
ADC_CHANNEL_COUNT = 21

ADC_CHANNEL_NAMES = (
    "VCC_12V", "VCC_M3V3", "VCC_3V3", "VCC_4V0", "VCC_1V8",
    "F1_VCCINT", "F1_AVCC", "F1_AVTT", "F1_VCCAUX",
    "F2_VCCINT", "F2_AVCC", "F2_AVTT", "F2_VCCAUX",
    "CUR_V_12V", "CUR_V_M3V3", "CUR_V_4V0", "CUR_V_F1VCCAUX",
    "CUR_V_F2VCCAUX", "F1_TEMP", "F2_TEMP", "TM4C_TEMP",
)


class McuPage(IntEnum):
    SYSTEM = 0x00
    POWER = 0x01
    ALARM = 0x02
    ADC = 0x03
    ADC_TARGET = 0x04
    PERSISTENT_LOG_INFO = 0x30
    PERSISTENT_LOG_DATA = 0x31
    CONTROL = 0x7F


class SystemReg(IntEnum):
    MAGIC = 0x00                 # char[4]
    MAP_MAJOR = 0x04             # uint8
    MAP_MINOR = 0x05             # uint8
    HARDWARE_REVISION = 0x06     # uint8
    ADC_CHANNEL_COUNT = 0x07     # uint8
    CAPABILITIES = 0x08          # uint32
    HEALTH_SUMMARY = 0x0C        # uint32
    BOARD_ID = 0x10              # uint32
    UPTIME_SECONDS = 0x14        # uint32
    RESET_CAUSE = 0x18           # uint32
    WATCHDOG_REGISTERED = 0x20   # uint32
    WATCHDOG_FED = 0x24          # uint32
    WATCHDOG_FAILED = 0x28       # uint32
    GIT_VERSION = 0x40           # char[20], NUL padded


class AdcReg(IntEnum):
    GENERATION = 0x00            # uint32
    SAMPLE_UPTIME_SECONDS = 0x04 # uint32
    VALID_CHANNELS = 0x08        # uint32 bitmap
    ERROR_CHANNELS = 0x0C        # uint32 bitmap
    CHANNEL_COUNT = 0x10         # uint8
    VALUES = 0x20                # binary16[21]


class AdcTargetReg(IntEnum):
    GENERATION = 0x00            # uint32
    VALID_CHANNELS = 0x04        # uint32 bitmap
    CHANNEL_COUNT = 0x08         # uint8
    VALUES = 0x20                # binary16[21]


class McuCapability(IntFlag):
    SYSTEM = 1 << 0
    POWER = 1 << 1
    ALARMS = 1 << 2
    ADC = 1 << 3
    ADC_TARGETS = 1 << 4
    PERSISTENT_LOG = 1 << 5
    CONTROLS = 1 << 6


class McuHealth(IntFlag):
    POWER_FAULT = 1 << 0
    TEMPERATURE_ALARM = 1 << 1
    VOLTAGE_ALARM = 1 << 2
    WATCHDOG_FAILURE = 1 << 3
    ADC_ERROR = 1 << 4


@dataclass(frozen=True)
class McuSystemInfo:
    map_major: int
    map_minor: int
    hardware_revision: int
    adc_channel_count: int
    capabilities: McuCapability
    health: McuHealth
    board_id: int
    uptime_seconds: int
    reset_cause: int
    watchdog_registered: int
    watchdog_fed: int
    watchdog_failed: int
    git_version: str


@dataclass(frozen=True)
class AdcReading:
    index: int
    name: str
    value: float
    valid: bool
    error: bool


@dataclass(frozen=True)
class AdcSnapshot:
    generation: int
    sample_uptime_seconds: int
    valid_channels: int
    error_channels: int
    readings: Tuple[AdcReading, ...]

    def __getitem__(self, name: str) -> AdcReading:
        for reading in self.readings:
            if reading.name == name:
                return reading
        raise KeyError(name)


@dataclass(frozen=True)
class AdcTarget:
    index: int
    name: str
    value: float
    valid: bool


@dataclass(frozen=True)
class AdcTargetSnapshot:
    generation: int
    valid_channels: int
    targets: Tuple[AdcTarget, ...]

    def __getitem__(self, name: str) -> AdcTarget:
        for target in self.targets:
            if target.name == name:
                return target
        raise KeyError(name)


class MCU(Device):
    """The command-module MCU at ProgCom device ``MC 0``.

    Register numbers use the normal package convention ``page << 8 | offset``.
    The initial implementation provides the read-only Phase-1 foundation.
    """

    def __init__(self, uart):
        super().__init__(uart, address=0)

    def _encode_command(self, reg: int, write: bool, payload: bytes = b"",
                        read_size: int = 1) -> bytes:
        return self._encode_ascii_command(
            "MC", 0, reg, write, payload, read_size
        )

    def _decode_response(self, raw: bytes) -> bytes:
        return self._decode_ascii_response(raw)

    @staticmethod
    def _reg(page: McuPage, offset: IntEnum) -> int:
        return (int(page) << 8) | int(offset)

    def _read_u8(self, page: McuPage, offset: IntEnum) -> int:
        return self.read_reg(self._reg(page, offset))[0]

    def _read_u32(self, page: McuPage, offset: IntEnum) -> int:
        return int.from_bytes(
            self.read_reg(self._reg(page, offset), size=4), "little"
        )

    def _check_map(self) -> Tuple[int, int]:
        base = self._reg(McuPage.SYSTEM, SystemReg.MAGIC)
        magic = self.read_reg(base, size=4)
        if magic != MCU_MAGIC:
            raise RuntimeError(
                f"unexpected MCU register-map magic {magic!r}; expected {MCU_MAGIC!r}"
            )
        version = self.read_reg(
            self._reg(McuPage.SYSTEM, SystemReg.MAP_MAJOR), size=2
        )
        if version[0] != MCU_MAP_MAJOR:
            raise RuntimeError(
                f"unsupported MCU register-map major version {version[0]}"
            )
        return version[0], version[1]

    @property
    def system_info(self) -> McuSystemInfo:
        major, minor = self._check_map()
        page = McuPage.SYSTEM
        return McuSystemInfo(
            map_major=major,
            map_minor=minor,
            hardware_revision=self._read_u8(page, SystemReg.HARDWARE_REVISION),
            adc_channel_count=self._read_u8(page, SystemReg.ADC_CHANNEL_COUNT),
            capabilities=McuCapability(self._read_u32(page, SystemReg.CAPABILITIES)),
            health=McuHealth(self._read_u32(page, SystemReg.HEALTH_SUMMARY)),
            board_id=self._read_u32(page, SystemReg.BOARD_ID),
            uptime_seconds=self._read_u32(page, SystemReg.UPTIME_SECONDS),
            reset_cause=self._read_u32(page, SystemReg.RESET_CAUSE),
            watchdog_registered=self._read_u32(page, SystemReg.WATCHDOG_REGISTERED),
            watchdog_fed=self._read_u32(page, SystemReg.WATCHDOG_FED),
            watchdog_failed=self._read_u32(page, SystemReg.WATCHDOG_FAILED),
            git_version=self.read_ascii(
                self._reg(page, SystemReg.GIT_VERSION), 20
            ),
        )

    def _read_half_array(self, page: McuPage, start: IntEnum,
                         count: int) -> Tuple[float, ...]:
        raw = self.read_block(self._reg(page, start), count * 2)
        return tuple(value[0] for value in struct.iter_unpack("<e", raw))

    def read_adc(self, retries: int = 3) -> AdcSnapshot:
        """Read one coherent ADC sample, retrying if publication changed."""
        if retries < 1:
            raise ValueError("retries must be at least one")
        page = McuPage.ADC
        for _ in range(retries):
            generation = self._read_u32(page, AdcReg.GENERATION)
            if generation & 1:
                continue
            sample_time = self._read_u32(page, AdcReg.SAMPLE_UPTIME_SECONDS)
            valid = self._read_u32(page, AdcReg.VALID_CHANNELS)
            errors = self._read_u32(page, AdcReg.ERROR_CHANNELS)
            count = self._read_u8(page, AdcReg.CHANNEL_COUNT)
            if count != ADC_CHANNEL_COUNT:
                raise RuntimeError(
                    f"MCU reports {count} ADC channels; expected {ADC_CHANNEL_COUNT}"
                )
            values = self._read_half_array(page, AdcReg.VALUES, count)
            if self._read_u32(page, AdcReg.GENERATION) == generation:
                readings = tuple(
                    AdcReading(i, ADC_CHANNEL_NAMES[i], value,
                               bool(valid & (1 << i)), bool(errors & (1 << i)))
                    for i, value in enumerate(values)
                )
                return AdcSnapshot(generation, sample_time, valid, errors, readings)
        raise RuntimeError("MCU ADC sample changed during every read attempt")

    @property
    def adc_readings(self) -> AdcSnapshot:
        return self.read_adc()

    def read_adc_targets(self, retries: int = 3) -> AdcTargetSnapshot:
        """Read one coherent configured voltage-target snapshot."""
        if retries < 1:
            raise ValueError("retries must be at least one")
        page = McuPage.ADC_TARGET
        for _ in range(retries):
            generation = self._read_u32(page, AdcTargetReg.GENERATION)
            if generation & 1:
                continue
            valid = self._read_u32(page, AdcTargetReg.VALID_CHANNELS)
            count = self._read_u8(page, AdcTargetReg.CHANNEL_COUNT)
            if count != ADC_CHANNEL_COUNT:
                raise RuntimeError(
                    f"MCU reports {count} ADC target slots; expected {ADC_CHANNEL_COUNT}"
                )
            values = self._read_half_array(page, AdcTargetReg.VALUES, count)
            if self._read_u32(page, AdcTargetReg.GENERATION) == generation:
                targets = tuple(
                    AdcTarget(i, ADC_CHANNEL_NAMES[i], value,
                              bool(valid & (1 << i)))
                    for i, value in enumerate(values)
                )
                return AdcTargetSnapshot(generation, valid, targets)
        raise RuntimeError("MCU ADC targets changed during every read attempt")

    @property
    def adc_targets(self) -> AdcTargetSnapshot:
        return self.read_adc_targets()
