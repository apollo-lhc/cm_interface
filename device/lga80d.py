from dataclasses import dataclass
from enum import IntEnum
from .base import Device
from ..utils import decode_linear11, decode_linear16u, encode_linear16u


@dataclass(frozen=True)
class LGA80DTelemetry:
    """A set of direct, non-atomic LGA80D telemetry readings."""

    input_voltage: float
    output_voltage: float
    output_current: float
    temperature: float
    switching_frequency: float
    status_word: int


@dataclass(frozen=True)
class LGA80DStatus:
    """Raw PMBus status registers from one LGA80D output page."""

    word: int
    vout: int
    iout: int
    input: int
    temperature: int
    cml: int
    manufacturer: int

    @property
    def has_faults(self) -> bool:
        return any((
            self.word,
            self.vout,
            self.iout,
            self.input,
            self.temperature,
            self.cml,
            self.manufacturer,
        ))

class LGA80DReg(IntEnum):
    """Safe monitoring and basic control commands for the LGA80D."""
    PAGE         = 0x00
    OPERATION    = 0x01
    ON_OFF_CONFIG = 0x02
    VOUT_COMMAND = 0x21
    STATUS_WORD  = 0x79
    STATUS_VOUT  = 0x7A
    STATUS_IOUT  = 0x7B
    STATUS_INPUT = 0x7C
    STATUS_TEMP  = 0x7D
    STATUS_CML   = 0x7E
    STATUS_MFR   = 0x80
    READ_VIN     = 0x88
    READ_VOUT    = 0x8B
    READ_IOUT    = 0x8C
    READ_TEMPERATURE_1 = 0x8D
    READ_TEMPERATURE_3 = 0x8F
    READ_FREQUENCY = 0x95

class LGA80D(Device):
    """Representation of an LGA80D device.

    Parameters
    ----------
    uart : UART
        Shared UART instance.
    address : int
        Device address on the bus.
    supply_name : str
        Textual identifier from the design (e.g. "F1VCCINT1").
    """
    def __init__(self, uart, address: int, supply_name: str):
        super().__init__(uart, address)
        self.supply_name = supply_name

    def _encode_command(self, reg: int, write: bool, payload: bytes = b"",
                        read_size: int = 1) -> bytes:
        return self._encode_ascii_command(
            "DC", self.address - 0x40, reg, write, payload, read_size
        )

    def _decode_response(self, raw: bytes) -> bytes:
        return self._decode_ascii_response(raw)

    @staticmethod
    def _paged_reg(command: int, page: int) -> int:
        if page not in (0, 1):
            raise ValueError("LGA80D page must be 0 or 1")
        return (page << 8) | int(command)

    def read_block(self, reg: int, length: int) -> bytes:
        raise NotImplementedError(
            "PMBus commands are not sequential registers; SMBus block reads "
            "require a device-specific transport"
        )

    def read_output_voltage(self, page: int = 0) -> float:
        raw = self.read_reg(self._paged_reg(LGA80DReg.READ_VOUT, page), size=2)
        return decode_linear16u(raw)

    def read_input_voltage(self, page: int = 0) -> float:
        raw = self.read_reg(self._paged_reg(LGA80DReg.READ_VIN, page), size=2)
        return decode_linear11(raw)

    def read_output_current(self, page: int = 0) -> float:
        raw = self.read_reg(self._paged_reg(LGA80DReg.READ_IOUT, page), size=2)
        return decode_linear11(raw)

    def read_temperature(self, page: int = 0, sensor: int = 1) -> float:
        if sensor == 1:
            command = LGA80DReg.READ_TEMPERATURE_1
        elif sensor == 3:
            command = LGA80DReg.READ_TEMPERATURE_3
        else:
            raise ValueError("LGA80D temperature sensor must be 1 or 3")
        raw = self.read_reg(self._paged_reg(command, page), size=2)
        return decode_linear11(raw)

    def read_switching_frequency(self, page: int = 0) -> float:
        raw = self.read_reg(self._paged_reg(LGA80DReg.READ_FREQUENCY, page), size=2)
        return decode_linear11(raw)

    def read_status_word(self, page: int = 0) -> int:
        raw = self.read_reg(self._paged_reg(LGA80DReg.STATUS_WORD, page), size=2)
        return int.from_bytes(raw, "little")

    def read_status(self, page: int = 0) -> LGA80DStatus:
        """Return the PMBus summary and category status registers."""
        return LGA80DStatus(
            word=self.read_status_word(page),
            vout=self.read_reg(self._paged_reg(LGA80DReg.STATUS_VOUT, page))[0],
            iout=self.read_reg(self._paged_reg(LGA80DReg.STATUS_IOUT, page))[0],
            input=self.read_reg(self._paged_reg(LGA80DReg.STATUS_INPUT, page))[0],
            temperature=self.read_reg(self._paged_reg(LGA80DReg.STATUS_TEMP, page))[0],
            cml=self.read_reg(self._paged_reg(LGA80DReg.STATUS_CML, page))[0],
            manufacturer=self.read_reg(self._paged_reg(LGA80DReg.STATUS_MFR, page))[0],
        )

    def read_telemetry(self, page: int = 0) -> LGA80DTelemetry:
        """Read current telemetry; unlike a future snapshot this is not atomic."""
        return LGA80DTelemetry(
            input_voltage=self.read_input_voltage(page),
            output_voltage=self.read_output_voltage(page),
            output_current=self.read_output_current(page),
            temperature=self.read_temperature(page),
            switching_frequency=self.read_switching_frequency(page),
            status_word=self.read_status_word(page),
        )

    @property
    def voltage(self) -> float:
        return self.read_output_voltage(page=0)

    @voltage.setter
    def voltage(self, v: float) -> None:
        self.set_output_voltage(v, page=0)

    def set_output_voltage(self, value: float, page: int = 0) -> None:
        """Set the commanded voltage for one output page."""
        reg = self._paged_reg(LGA80DReg.VOUT_COMMAND, page)
        self.write_reg(reg, encode_linear16u(value))
