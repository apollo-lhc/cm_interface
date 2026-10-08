"""Typed access to the command-module MCU's own ProgCom register area.

Register layout follows ``MCU_REGISTER_MAP.md`` (map major version 1). That
file is the hand-maintained source of truth shared with the firmware's
``MCU_Reg.h`` — keep this module in sync with it, not the other way around.
"""

import math
import struct
from enum import IntEnum, IntFlag
from typing import Optional, Tuple

from ..compat import dataclass
from ..errors import (
    McuCapabilityUnavailable,
    McuCoherencyError,
    McuMagicError,
    McuMapVersionError,
)
from .base import Device


MCU_MAGIC = b"CMCU"
MCU_MAP_MAJOR = 1
ADC_CHANNEL_COUNT = 21
POWER_SUPPLY_ARRAY_LEN = 12
PERSISTENT_LOG_CAPACITY_WORDS = 64
SYS_BUILD_TIME_LEN = 24
MCU_MAP_MINOR_FF_MASKS = 1   # map minor at which SystemReg 0x54+ became valid

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
    CONFIG = 0x05
    RUNTIME = 0x06
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
    FF_USER_MASK = 0x54          # uint32 bitmask, minor >= 1
    FF_PRESENT_MASK = 0x58       # uint32 bitmask, minor >= 1
    BUILD_TYPE = 0x5C            # uint8 McuBuildType, minor >= 1
    BUILD_TIME = 0x60            # char[24] NUL padded, minor >= 1


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


class ConfigReg(IntEnum):
    ALARM_TEMP_FF = 0x00         # int16, degrees C
    ALARM_TEMP_DCDC = 0x02       # int16
    ALARM_TEMP_TM4C = 0x04       # int16
    ALARM_TEMP_FPGA = 0x06       # int16
    ALARM_VOLT_THRESHOLD = 0x08  # uint16, centi-percent on the wire


CONFIG_FIELD_WIDTHS = {
    ConfigReg.ALARM_TEMP_FF: 2,
    ConfigReg.ALARM_TEMP_DCDC: 2,
    ConfigReg.ALARM_TEMP_TM4C: 2,
    ConfigReg.ALARM_TEMP_FPGA: 2,
    ConfigReg.ALARM_VOLT_THRESHOLD: 2,
}


# Write clamps for the Config page. The firmware enforces the same bounds
# (``CFG_TEMP_*`` / ``CFG_VOLT_*`` in MCU_Reg.h); the client checks first so a
# dangerous value fails before it reaches the wire. Reads are not clamped.
ALARM_TEMP_MIN_C = 50
ALARM_TEMP_MAX_C = 100
ALARM_VOLT_CPCT_MIN = 100     # 1 %
ALARM_VOLT_CPCT_MAX = 1000    # 10 %


class AlarmTempDevice(IntEnum):
    """Matches the firmware's ``enum device`` (``Tasks.h``): FF, DCDC, TM4C, FPGA."""

    FF = 0
    DCDC = 1
    TM4C = 2
    FPGA = 3


_ALARM_TEMP_REG = {
    AlarmTempDevice.FF: ConfigReg.ALARM_TEMP_FF,
    AlarmTempDevice.DCDC: ConfigReg.ALARM_TEMP_DCDC,
    AlarmTempDevice.TM4C: ConfigReg.ALARM_TEMP_TM4C,
    AlarmTempDevice.FPGA: ConfigReg.ALARM_TEMP_FPGA,
}


class RuntimeReg(IntEnum):
    HEAP_FREE = 0x00                      # uint32 bytes
    HEAP_MIN_EVER_FREE = 0x04             # uint32 bytes
    HEAP_TOTAL = 0x08                     # uint32 bytes
    SYSTEM_STACK_UNTOUCHED_WORDS = 0x0C   # uint32 words; falling = worse
    SYSTEM_STACK_TOTAL_WORDS = 0x10       # uint32 words
    ZYNQMON_TRANSMIT_ENABLED = 0x14       # uint8 0/1
    FPGA_DONE = 0x15                      # uint8 bitmap, bit0 = F1, bit1 = F2
    RTC_DATE = 0x18                       # uint32 packed, see McuRtc
    RTC_TIME = 0x1C                       # uint32 packed, see McuRtc


class ControlReg(IntEnum):
    COMMAND = 0x00                # uint8, write-only


class McuControlCommand(IntEnum):
    ASSERT_PROGCOM_POWER_INHIBIT = 1
    RELEASE_PROGCOM_POWER_INHIBIT = 2
    CLEAR_POWER_FAULT = 3
    CLEAR_ALARM_LATCHES = 4
    ZYNQMON_ENABLE_TRANSMIT = 5
    ZYNQMON_DISABLE_TRANSMIT = 6   # sticky: nothing re-enables it but the CLI or a reboot


class McuCapability(IntFlag):
    SYSTEM = 1 << 0
    POWER = 1 << 1
    ALARMS = 1 << 2
    ADC = 1 << 3
    PERSISTENT_LOG = 1 << 5
    CONTROLS = 1 << 6
    CONFIG = 1 << 7
    RUNTIME = 1 << 8
    CONFIG_WRITE = 1 << 9


class McuBuildType(IntEnum):
    RELEASE = 0
    DEBUG = 1


class FpgaDone(IntFlag):
    F1 = 1 << 0
    F2 = 1 << 1


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
    # Appended, never inserted: preserves positional construction under the
    # Python 3.6 namedtuple fallback. All None when map_minor < 1.
    ff_user_mask: Optional[int] = None
    ff_present_mask: Optional[int] = None
    build_type: Optional[McuBuildType] = None
    build_time: Optional[str] = None


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
        # Under the Python 3.6 compat fallback this class is a namedtuple, and
        # ``self.readings`` itself calls ``self[0]``; without this guard that
        # recurses forever.
        if not isinstance(name, str):
            return tuple.__getitem__(self, name)
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
class McuAlarmConfig:
    """The alarm thresholds. Temperatures are signed: EEPROM content set
    earlier through the CLI can lie outside any range the wire would accept."""

    alarm_temp_ff: int
    alarm_temp_dcdc: int
    alarm_temp_tm4c: int
    alarm_temp_fpga: int
    alarm_volt_threshold_percent: float


@dataclass(frozen=True)
class McuRtc:
    valid: bool
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int

    def isoformat(self) -> str:
        if not self.valid:
            return "unset"
        return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}".format(
            self.year, self.month, self.day,
            self.hour, self.minute, self.second)


@dataclass(frozen=True)
class McuRuntimeInfo:
    heap_free_bytes: int
    heap_min_ever_free_bytes: int
    heap_total_bytes: int
    system_stack_untouched_words: int
    system_stack_total_words: int
    zynqmon_transmit_enabled: bool
    fpga_done: FpgaDone
    rtc: McuRtc


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

    # Class-level default so test doubles that skip ``__init__`` still work.
    _capabilities = None

    _CAPABILITY_FOR_PAGE = {
        McuPage.POWER: McuCapability.POWER,
        McuPage.ALARM: McuCapability.ALARMS,
        McuPage.ADC: McuCapability.ADC,
        McuPage.CONFIG: McuCapability.CONFIG,
        McuPage.RUNTIME: McuCapability.RUNTIME,
        McuPage.PERSISTENT_LOG_INFO: McuCapability.PERSISTENT_LOG,
        McuPage.PERSISTENT_LOG_DATA: McuCapability.PERSISTENT_LOG,
        McuPage.CONTROL: McuCapability.CONTROLS,
    }

    def __init__(self, uart):
        super().__init__(uart, address=0)
        self._capabilities = None

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

    def _read_u16(self, page: McuPage, offset: IntEnum) -> int:
        return struct.unpack("<H", self.read_reg(self._reg(page, offset), size=2))[0]

    def _read_i16(self, page: McuPage, offset: IntEnum) -> int:
        return struct.unpack("<h", self.read_reg(self._reg(page, offset), size=2))[0]

    def _write_i16(self, page: McuPage, offset: IntEnum, value: int) -> None:
        self.write_reg(self._reg(page, offset), struct.pack("<h", value))

    def _write_u16(self, page: McuPage, offset: IntEnum, value: int) -> None:
        self.write_reg(self._reg(page, offset), struct.pack("<H", value))

    def _read_u32(self, page: McuPage, offset: IntEnum) -> int:
        return int.from_bytes(
            self.read_reg(self._reg(page, offset), size=4), "little"
        )

    def capabilities(self, refresh: bool = False) -> McuCapability:
        """Return the firmware capability mask, cached after the first read.

        The cache goes stale across a reflash; pass ``refresh=True`` to re-read.
        """
        if refresh or self._capabilities is None:
            self._capabilities = McuCapability(
                self._read_u32(McuPage.SYSTEM, SystemReg.CAPABILITIES)
            )
        return self._capabilities

    def _require_page(self, page: McuPage, check: bool = True) -> None:
        """Refuse to touch a page the firmware does not advertise.

        ``check=False`` is the escape hatch for probing a page whose bit is not
        yet set. ``system_info`` never gates: it is how the mask is discovered.
        """
        needed = self._CAPABILITY_FOR_PAGE.get(page)
        if check and needed is not None and not self.capabilities() & needed:
            raise McuCapabilityUnavailable(
                "firmware does not advertise {}".format(needed.name)
            )

    def _check_map(self) -> Tuple[int, int]:
        base = self._reg(McuPage.SYSTEM, SystemReg.MAGIC)
        magic = self.read_reg(base, size=4)
        if magic != MCU_MAGIC:
            raise McuMagicError(
                f"unexpected MCU register-map magic {magic!r}; expected {MCU_MAGIC!r}"
            )
        major = self._read_u8(McuPage.SYSTEM, SystemReg.MAP_MAJOR)
        minor = self._read_u8(McuPage.SYSTEM, SystemReg.MAP_MINOR)
        if major != MCU_MAP_MAJOR:
            raise McuMapVersionError(
                f"unsupported MCU register-map major version {major}"
            )
        return major, minor

    @property
    def system_info(self) -> McuSystemInfo:
        major, minor = self._check_map()
        page = McuPage.SYSTEM
        # Map minor 0 firmware answers 0x54+ with ``invalid MCU address``, so
        # the new fields are not even requested.
        extra = {}
        if minor >= MCU_MAP_MINOR_FF_MASKS:
            user_mask, present_mask = self._read_firefly_masks()
            extra = dict(
                ff_user_mask=user_mask,
                ff_present_mask=present_mask,
                build_type=McuBuildType(self._read_u8(page, SystemReg.BUILD_TYPE)),
                build_time=self.read_ascii(
                    self._reg(page, SystemReg.BUILD_TIME), SYS_BUILD_TIME_LEN
                ),
            )
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
            **extra
        )

    def _read_firefly_masks(self, retries: int = 3) -> Tuple[int, int]:
        """Read the Firefly user/present masks as a consistent pair.

        The two masks are separate 4-byte transactions and are not updated
        atomically by firmware, so read both twice and retry on disagreement.
        """
        page = McuPage.SYSTEM

        def once():
            return (self._read_u32(page, SystemReg.FF_USER_MASK),
                    self._read_u32(page, SystemReg.FF_PRESENT_MASK))

        for _ in range(max(1, retries)):
            first = once()
            if once() == first:
                return first
        raise McuCoherencyError("MCU Firefly masks changed during every read attempt")

    def read_adc(self, check_capability: bool = True) -> AdcSnapshot:
        """Read the ADC sample array.

        One 4-byte-aligned atomic ``float`` read per channel means nothing
        can tear, so there is no generation counter here (unlike
        :meth:`read_power`). A channel with no current reading is ``NaN``.
        """
        page = McuPage.ADC
        self._require_page(page, check_capability)
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

    def read_power(self, retries: int = 3,
                   check_capability: bool = True) -> PowerSnapshot:
        """Read one coherent Power snapshot, retrying if publication changed."""
        if retries < 1:
            raise ValueError("retries must be at least one")
        page = McuPage.POWER
        self._require_page(page, check_capability)
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
        raise McuCoherencyError("MCU power snapshot changed during every read attempt")

    @property
    def power(self) -> PowerSnapshot:
        return self.read_power()

    def read_alarm(self, check_capability: bool = True) -> AlarmSnapshot:
        """Read the Alarm page.

        No generation counter: the two FSM-state bytes, the temperature
        status/warning-latch pair, and the three voltage-alarm bytes are each
        updated atomically as a group by firmware. Note the status/latch pair
        is fetched as an 8-byte ``read_block``, i.e. two 4-byte wire
        transactions -- the firmware-side grouping holds, but the pair is not
        read atomically over the wire. Staleness between the temperature and
        voltage groups is ordinary staleness between two independently
        scheduled tasks, not tearing.
        """
        page = McuPage.ALARM
        self._require_page(page, check_capability)
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

    def read_alarm_config(self, check_capability: bool = True) -> McuAlarmConfig:
        """Read the Config page (alarm thresholds), read-only.

        Five independent 16-bit policy values, each read in one transaction, so
        no generation counter is needed and nothing can tear.
        """
        page = McuPage.CONFIG
        self._require_page(page, check_capability)
        temps = {
            device: self._read_i16(page, reg)
            for device, reg in _ALARM_TEMP_REG.items()
        }
        centi_percent = self._read_u16(page, ConfigReg.ALARM_VOLT_THRESHOLD)
        return McuAlarmConfig(
            alarm_temp_ff=temps[AlarmTempDevice.FF],
            alarm_temp_dcdc=temps[AlarmTempDevice.DCDC],
            alarm_temp_tm4c=temps[AlarmTempDevice.TM4C],
            alarm_temp_fpga=temps[AlarmTempDevice.FPGA],
            alarm_volt_threshold_percent=centi_percent / 100.0,
        )

    @property
    def alarm_config(self) -> McuAlarmConfig:
        return self.read_alarm_config()

    def _require_config_write(self, check: bool) -> None:
        self._require_page(McuPage.CONFIG, check)
        if check and not self.capabilities() & McuCapability.CONFIG_WRITE:
            raise McuCapabilityUnavailable(
                "firmware does not advertise {}".format(McuCapability.CONFIG_WRITE.name)
            )

    def set_alarm_temperature(self, device: AlarmTempDevice, celsius: int,
                              check_capability: bool = True) -> None:
        """Set one over-temperature alarm threshold; persists to EEPROM.

        HAZARD: the temperature alarm task powers the board down when a device
        exceeds its threshold. Raising a threshold persistently reduces thermal
        protection (it survives reboot). Lowering one can force an immediate
        power-down that no inhibit bit reflects -- e.g. 50 C on the FPGA
        (default 81) or DCDC (default 70) threshold of a loaded board.

        ``celsius`` must be an integer in ``ALARM_TEMP_MIN_C..ALARM_TEMP_MAX_C``;
        anything else raises ``ValueError`` and sends nothing. A full firmware
        EEPROM queue surfaces as an error from :meth:`write_reg`; nothing changed
        and the call may be retried.
        """
        device = AlarmTempDevice(device)
        if isinstance(celsius, bool) or int(celsius) != celsius:
            raise ValueError("alarm temperature must be a whole number of degrees C")
        celsius = int(celsius)
        if not ALARM_TEMP_MIN_C <= celsius <= ALARM_TEMP_MAX_C:
            raise ValueError(
                "alarm temperature {} C outside {}..{} C".format(
                    celsius, ALARM_TEMP_MIN_C, ALARM_TEMP_MAX_C)
            )
        self._require_config_write(check_capability)
        self._write_i16(McuPage.CONFIG, _ALARM_TEMP_REG[device], celsius)

    def set_alarm_voltage_threshold_percent(self, percent: float,
                                            check_capability: bool = True) -> None:
        """Set the voltage-alarm threshold in percent (1-10); persists to EEPROM.

        Raising it persistently widens the band in which supply voltage errors
        go unflagged. Out-of-range or NaN values raise ``ValueError`` and send
        nothing. Queue-full behaviour is as for :meth:`set_alarm_temperature`.
        """
        # Compare as floats first: NaN fails every comparison and inf never
        # reaches int(), so neither can slip through or raise OverflowError.
        scaled = percent * 100
        if not (ALARM_VOLT_CPCT_MIN <= scaled <= ALARM_VOLT_CPCT_MAX):
            raise ValueError(
                "voltage threshold {!r} % outside {}..{} %".format(
                    percent, ALARM_VOLT_CPCT_MIN / 100.0, ALARM_VOLT_CPCT_MAX / 100.0)
            )
        centi_percent = int(round(scaled))
        self._require_config_write(check_capability)
        self._write_u16(McuPage.CONFIG, ConfigReg.ALARM_VOLT_THRESHOLD, centi_percent)

    def read_runtime(self, retries: int = 3,
                     check_capability: bool = True) -> McuRuntimeInfo:
        """Read the Runtime page.

        The RTC is two independent 4-byte fields, so neither tears inside a
        transaction, but they are read in separate transactions. Read time,
        date, then time again, and retry if the time moved.
        """
        page = McuPage.RUNTIME
        self._require_page(page, check_capability)
        for _ in range(max(1, retries)):
            time_raw = self._read_u32(page, RuntimeReg.RTC_TIME)
            date_raw = self._read_u32(page, RuntimeReg.RTC_DATE)
            if self._read_u32(page, RuntimeReg.RTC_TIME) == time_raw:
                break
        else:
            raise McuCoherencyError("MCU RTC changed during every read attempt")
        rtc = McuRtc(
            valid=bool((time_raw >> 24) & 1),
            year=(date_raw >> 16) & 0xFFFF,
            month=(date_raw >> 8) & 0xFF,
            day=date_raw & 0xFF,
            hour=(time_raw >> 16) & 0xFF,
            minute=(time_raw >> 8) & 0xFF,
            second=time_raw & 0xFF,
        )
        return McuRuntimeInfo(
            heap_free_bytes=self._read_u32(page, RuntimeReg.HEAP_FREE),
            heap_min_ever_free_bytes=self._read_u32(page, RuntimeReg.HEAP_MIN_EVER_FREE),
            heap_total_bytes=self._read_u32(page, RuntimeReg.HEAP_TOTAL),
            system_stack_untouched_words=self._read_u32(
                page, RuntimeReg.SYSTEM_STACK_UNTOUCHED_WORDS
            ),
            system_stack_total_words=self._read_u32(
                page, RuntimeReg.SYSTEM_STACK_TOTAL_WORDS
            ),
            zynqmon_transmit_enabled=bool(
                self._read_u8(page, RuntimeReg.ZYNQMON_TRANSMIT_ENABLED)
            ),
            fpga_done=FpgaDone(self._read_u8(page, RuntimeReg.FPGA_DONE)),
            rtc=rtc,
        )

    @property
    def runtime(self) -> McuRuntimeInfo:
        return self.read_runtime()

    def read_persistent_log_info(self,
                                 check_capability: bool = True) -> PersistentLogInfo:
        page = McuPage.PERSISTENT_LOG_INFO
        self._require_page(page, check_capability)
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

    def read_persistent_log_entries(self,
                                    check_capability: bool = True) -> Tuple[int, ...]:
        """Read the raw 32-bit log words, logical index 0 = newest.

        Firmware publishes no valid-entry count: scan all entries and filter
        erased/empty sentinels yourself.
        """
        page = McuPage.PERSISTENT_LOG_DATA
        self._require_page(page, check_capability)
        raw = self.read_block(self._reg(page, 0x00), PERSISTENT_LOG_CAPACITY_WORDS * 4)
        return struct.unpack(f"<{PERSISTENT_LOG_CAPACITY_WORDS}I", raw)

    def send_control(self, command: McuControlCommand,
                     check_capability: bool = True) -> None:
        """Send a one-byte command to the write-only Control page.

        A full command queue on the firmware side surfaces as an
        ``MCU_REG_QUEUE_FULL``-style error from :meth:`write_reg`, not a
        stall; there is no atomic multi-queue delivery for any command here.
        """
        page = McuPage.CONTROL
        self._require_page(page, check_capability)
        self.write_reg(self._reg(page, ControlReg.COMMAND), bytes([int(command)]))
