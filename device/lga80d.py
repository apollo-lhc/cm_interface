from enum import IntEnum
from .base import Device

class LGA80DReg(IntEnum):
    """Registers for the LGA80D module (partial)."""
    PAGE         = 0x00
    VOUT_COMMAND = 0x21
    READ_VOUT    = 0x8B
    # Extend with full register map from the datasheet.

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

    @property
    def voltage(self) -> float:
        raw = self.read_reg(LGA80DReg.READ_VOUT, size=2)
        return int.from_bytes(raw, "little") * (2 ** -13)

    @voltage.setter
    def voltage(self, v: float) -> None:
        raw = round(v * (2 ** 13))
        if not 0 <= raw <= 0xFFFF:
            raise ValueError("voltage is outside the Linear16 representable range")
        self.write_reg(LGA80DReg.VOUT_COMMAND, raw.to_bytes(2, "little"))
