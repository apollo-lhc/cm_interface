"""Typed access to the command-module MCU's own ProgCom register area.

Register layout follows ``MCU_REGISTER_MAP.md`` (map major version 1). That
file is the hand-maintained source of truth shared with the firmware's
``MCU_Reg.h`` — keep this module in sync with it, not the other way around.
"""

import math
import struct
from enum import IntEnum, IntFlag
from typing import Tuple

from ..compat import dataclass
from .base import Device


MCU_MAGIC = b"CMCU"
MCU_MAP_MAJOR = 1
ADC_CHANNEL_COUNT = 21
POWER_SUPPLY_ARRAY_LEN = 12
PERSISTENT_LOG_CAPACITY_WORDS = 64

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
    RESET_CAUSE = 0x18           # uint32, raw uncleared SysCtl value
    GIT_VERSION = 0x40           # char[20], NUL padded


class PowerReg(IntEnum):
    GENERATION = 0x00            # uint32
    FSM_STATE = 0x04             # uint8
    FLAGS = 0x05                 # uint8 bitmap
    LIVE_PG_MASK = 0x08          # uint32
    EXPECTED_PG_MASK = 0x0C      # uint32
    SOFTWARE_IGNORE_MASK = 0x10  # uint32
    FAILED_MASK = 0x14           # uint32
    SUPPLY_COUNT = 0x18          # uint8
    SUPPLY_STATE = 0x1C          # uint8[12], index = PG-pin index


class AlarmReg(IntEnum):
    TEMP_TASK_STATE = 0x00        # uint8
    VOLTAGE_TASK_STATE = 0x01     # uint8
    TEMP_STATUS = 0x04            # uint32 bitmap
    TEMP_WARN_LATCH = 0x08         # uint32 bitmap
    VOLTAGE_ALARM_GENERAL = 0x0C  # uint8 bitmap
    VOLTAGE_ALARM_F1 = 0x0D       # uint8 bitmap
    VOLTAGE_ALARM_F2 = 0x0E       # uint8 bitmap


class AdcReg(IntEnum):
    VALUES = 0x00                 # binary16[21]


class PersistentLogInfoReg(IntEnum):
    FORMAT_VERSION = 0x00        # uint32
    CAPACITY_WORDS = 0x04        # uint32
    GENERATION = 0x08            # uint32, mutation generation
    LATEST_ERROR_CODE = 0x0C     # uint32
    CONTINUATION_COUNT = 0x10    # uint32


class ControlReg(IntEnum):
    COMMAND = 0x00                # uint8, write-only


class McuControlCommand(IntEnum):
    ASSERT_PROGCOM_POWER_INHIBIT = 1
    RELEASE_PROGCOM_POWER_INHIBIT = 2
    CLEAR_POWER_FAULT = 3
    CLEAR_ALARM_LATCHES = 4


class McuCapability(IntFlag):
    SYSTEM = 1 << 0
    POWER = 1 << 1
    ALARMS = 1 << 2
    ADC = 1 << 3
    PERSISTENT_LOG = 1 << 5
    CONTROLS = 1 << 6


class McuHealth(IntFlag):
    POWER_FAULT = 1 << 0
    TEMPERATURE_ALARM = 1 << 1
    VOLTAGE_ALARM = 1 << 2
    ADC_ERROR = 1 << 3


class McuResetCause(IntFlag):
    """TI TM4C1290 ``SYSCTL_RESC`` reset-cause bits."""

    EXT = 1 << 0           # external RST pin assertion
    POR = 1 << 1           # power-on reset
    BOR = 1 << 2           # VDD or VDDA brown-out reset
    WDT0 = 1 << 3          # Watchdog Timer 0 timeout
    SW = 1 << 4            # software-requested system reset
    WDT1 = 1 << 5          # Watchdog Timer 1 timeout
    HSSR = 1 << 12         # Hardware System Service Request reset
    MOSCFAIL = 1 << 16     # main-oscillator validation failure


RESET_CAUSE_DESCRIPTIONS = (
    (McuResetCause.EXT, "external reset pin asserted"),
    (McuResetCause.POR, "power-on reset"),
    (McuResetCause.BOR, "VDD or VDDA brown-out reset"),
    (McuResetCause.WDT0, "Watchdog Timer 0 timed out"),
    (McuResetCause.SW, "software-requested system reset"),
    (McuResetCause.WDT1, "Watchdog Timer 1 timed out"),
    (McuResetCause.HSSR, "Hardware System Service Request reset"),
    (McuResetCause.MOSCFAIL, "main-oscillator validation failure"),
)
RESET_CAUSE_KNOWN_MASK = sum(int(flag) for flag, _ in RESET_CAUSE_DESCRIPTIONS)


def describe_reset_cause(cause: McuResetCause) -> Tuple[str, ...]:
    """Return readable details for all set ``SYSCTL_RESC`` bits.

    RESC bits are sticky across reset sequences until cleared, so multiple
    descriptions may be returned. Unknown bits are retained and reported.
    """
    raw = int(cause)
    details = [
        "{}: {}".format(flag.name, description)
        for flag, description in RESET_CAUSE_DESCRIPTIONS
        if raw & int(flag)
    ]
    unknown = raw & ~RESET_CAUSE_KNOWN_MASK & 0xFFFFFFFF
    if unknown:
        details.append("UNKNOWN: reserved/unrecognized bits 0x{:08X}".format(unknown))
    if not details:
        details.append("none reported")
    return tuple(details)


class PowerFsmState(IntEnum):
    POWER_FAILURE = 0
    POWER_INIT = 1
    POWER_DOWN = 2
    POWER_OFF = 3
    POWER_L1ON = 4
    POWER_L2ON = 5
    POWER_L3ON = 6
    POWER_L4ON = 7
    POWER_L5ON = 8
    POWER_L6ON = 9
    POWER_ON = 10


class PowerFlags(IntFlag):
    BLADE_POWER_EN = 1 << 0
    CLI_INHIBIT = 1 << 1
    PROGCOM_INHIBIT = 1 << 2
    POWER_FAULT_LATCH = 1 << 3
    ALARM_SHUTDOWN_LATCH = 1 << 4
    F1_ENABLE = 1 << 5
    F2_ENABLE = 1 << 6


class PerSupplyState(IntEnum):
    PWR_UNKNOWN = 0
    PWR_ON = 1
    PWR_OFF = 2
    PWR_DISABLED = 3
    PWR_FAILED = 4


class AlarmTaskState(IntEnum):
    ALM_INIT = 0
    ALM_NORMAL = 1
    ALM_WARN = 2
    ALM_FAULT_ERRORING = 3
    ALM_FAULT_ERROR_CLEARED = 4


class TemperatureAlarmBit(IntFlag):
    TM4C = 1 << 0
    FIREFLY = 1 << 1
    FPGA = 1 << 2
    DCDC = 1 << 3


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
    reset_cause: McuResetCause
    git_version: str


@dataclass(frozen=True)
class AdcReading:
    index: int
    name: str
    value: float

    @property
    def valid(self) -> bool:
        return not math.isnan(self.value)


@dataclass(frozen=True)
class AdcSnapshot:
    readings: Tuple[AdcReading, ...]

    def __getitem__(self, name: str) -> AdcReading:
        for reading in self.readings:
            if reading.name == name:
                return reading
        raise KeyError(name)


@dataclass(frozen=True)
class PowerSnapshot:
    generation: int
    fsm_state: PowerFsmState
    flags: PowerFlags
    live_pg_mask: int
    expected_pg_mask: int
    software_ignore_mask: int
    failed_mask: int
    supply_count: int
    supply_states: Tuple[PerSupplyState, ...]


@dataclass(frozen=True)
class AlarmSnapshot:
    temp_task_state: AlarmTaskState
    voltage_task_state: AlarmTaskState
    temp_status: TemperatureAlarmBit
    temp_warn_latch: TemperatureAlarmBit
    voltage_alarm_general: int
    voltage_alarm_f1: int
    voltage_alarm_f2: int


@dataclass(frozen=True)
class PersistentLogInfo:
    format_version: int
    capacity_words: int
    generation: int
    latest_error_code: int
    continuation_count: int


class MCU(Device):
    """The command-module MCU at ProgCom device ``MC 0``.

    Register numbers use the normal package convention ``page << 8 | offset``.
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
        major = self._read_u8(McuPage.SYSTEM, SystemReg.MAP_MAJOR)
        minor = self._read_u8(McuPage.SYSTEM, SystemReg.MAP_MINOR)
        if major != MCU_MAP_MAJOR:
            raise RuntimeError(
                f"unsupported MCU register-map major version {major}"
            )
        return major, minor

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
            reset_cause=McuResetCause(
                self._read_u32(page, SystemReg.RESET_CAUSE)
            ),
            git_version=self.read_ascii(
                self._reg(page, SystemReg.GIT_VERSION), 20
            ),
        )

    def read_adc(self) -> AdcSnapshot:
        """Read the ADC sample array.

        One 4-byte-aligned atomic ``float`` read per channel means nothing
        can tear, so there is no generation counter here (unlike
        :meth:`read_power`). A channel with no current reading is ``NaN``.
        """
        page = McuPage.ADC
        raw = self.read_block(self._reg(page, AdcReg.VALUES), ADC_CHANNEL_COUNT * 2)
        values = tuple(value[0] for value in struct.iter_unpack("<e", raw))
        readings = tuple(
            AdcReading(i, ADC_CHANNEL_NAMES[i], value)
            for i, value in enumerate(values)
        )
        return AdcSnapshot(readings)

    @property
    def adc_readings(self) -> AdcSnapshot:
        return self.read_adc()

    def read_power(self, retries: int = 3) -> PowerSnapshot:
        """Read one coherent Power snapshot, retrying if publication changed."""
        if retries < 1:
            raise ValueError("retries must be at least one")
        page = McuPage.POWER
        for _ in range(retries):
            generation = self._read_u32(page, PowerReg.GENERATION)
            if generation & 1:
                continue
            fsm_state = PowerFsmState(self._read_u8(page, PowerReg.FSM_STATE))
            flags = PowerFlags(self._read_u8(page, PowerReg.FLAGS))
            live_pg_mask = self._read_u32(page, PowerReg.LIVE_PG_MASK)
            expected_pg_mask = self._read_u32(page, PowerReg.EXPECTED_PG_MASK)
            software_ignore_mask = self._read_u32(page, PowerReg.SOFTWARE_IGNORE_MASK)
            failed_mask = self._read_u32(page, PowerReg.FAILED_MASK)
            supply_count = self._read_u8(page, PowerReg.SUPPLY_COUNT)
            raw_states = self.read_block(
                self._reg(page, PowerReg.SUPPLY_STATE), POWER_SUPPLY_ARRAY_LEN
            )
            supply_states = tuple(PerSupplyState(value) for value in raw_states)
            if self._read_u32(page, PowerReg.GENERATION) == generation:
                return PowerSnapshot(
                    generation, fsm_state, flags, live_pg_mask, expected_pg_mask,
                    software_ignore_mask, failed_mask, supply_count, supply_states,
                )
        raise RuntimeError("MCU power snapshot changed during every read attempt")

    @property
    def power(self) -> PowerSnapshot:
        return self.read_power()

    def read_alarm(self) -> AlarmSnapshot:
        """Read the Alarm page.

        No generation counter: the two FSM-state bytes, the temperature
        status/warning-latch pair, and the three voltage-alarm bytes are each
        updated atomically as a group by firmware, so each group is read in
        one transaction. Staleness between the temperature and voltage groups
        is ordinary staleness between two independently scheduled tasks, not
        tearing.
        """
        page = McuPage.ALARM
        temp_task_state = AlarmTaskState(self._read_u8(page, AlarmReg.TEMP_TASK_STATE))
        voltage_task_state = AlarmTaskState(
            self._read_u8(page, AlarmReg.VOLTAGE_TASK_STATE)
        )
        temp_block = self.read_block(self._reg(page, AlarmReg.TEMP_STATUS), 8)
        temp_status, temp_warn_latch = struct.unpack("<II", temp_block)
        voltage_general = self._read_u8(page, AlarmReg.VOLTAGE_ALARM_GENERAL)
        voltage_f1 = self._read_u8(page, AlarmReg.VOLTAGE_ALARM_F1)
        voltage_f2 = self._read_u8(page, AlarmReg.VOLTAGE_ALARM_F2)
        return AlarmSnapshot(
            temp_task_state,
            voltage_task_state,
            TemperatureAlarmBit(temp_status),
            TemperatureAlarmBit(temp_warn_latch),
            voltage_general,
            voltage_f1,
            voltage_f2,
        )

    @property
    def alarm(self) -> AlarmSnapshot:
        return self.read_alarm()

    def read_persistent_log_info(self) -> PersistentLogInfo:
        page = McuPage.PERSISTENT_LOG_INFO
        return PersistentLogInfo(
            format_version=self._read_u32(page, PersistentLogInfoReg.FORMAT_VERSION),
            capacity_words=self._read_u32(page, PersistentLogInfoReg.CAPACITY_WORDS),
            generation=self._read_u32(page, PersistentLogInfoReg.GENERATION),
            latest_error_code=self._read_u32(
                page, PersistentLogInfoReg.LATEST_ERROR_CODE
            ),
            continuation_count=self._read_u32(
                page, PersistentLogInfoReg.CONTINUATION_COUNT
            ),
        )

    def read_persistent_log_entries(self) -> Tuple[int, ...]:
        """Read the raw 32-bit log words, logical index 0 = newest.

        Firmware publishes no valid-entry count: scan all entries and filter
        erased/empty sentinels yourself.
        """
        page = McuPage.PERSISTENT_LOG_DATA
        raw = self.read_block(self._reg(page, 0x00), PERSISTENT_LOG_CAPACITY_WORDS * 4)
        return struct.unpack(f"<{PERSISTENT_LOG_CAPACITY_WORDS}I", raw)

    def send_control(self, command: McuControlCommand) -> None:
        """Send a one-byte command to the write-only Control page.

        A full command queue on the firmware side surfaces as an
        ``MCU_REG_QUEUE_FULL``-style error from :meth:`write_reg`, not a
        stall; there is no atomic multi-queue delivery for any command here.
        """
        page = McuPage.CONTROL
        self.write_reg(self._reg(page, ControlReg.COMMAND), bytes([int(command)]))
