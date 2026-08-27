from enum import IntEnum
from .base import Device
from ..utils import pack_uint16, unpack_uint16, crc8

class LGA80DReg(IntEnum):
    """Registers for the LGA80D module (partial)."""
    SUPPLY   = 0x00   # Identifier of the supply (read‑only)
    VALUE    = 0x01   # Measured voltage/value (16‑bit)
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

    def _encode_command(self, reg: int, write: bool, payload: bytes = b"") -> bytes:
        cmd = bytes([
            self.address,
            0x01 if write else 0x00,
            (reg >> 8) & 0xFF,
            reg & 0xFF,
            len(payload)
        ]) + payload
        return cmd + crc8(cmd)

    def _decode_response(self, raw: bytes) -> bytes:
        return raw[:-1]

    @property
    def voltage(self) -> float:
        raw = self.read_reg(LGA80DReg.VALUE)
        # LSB assumed to be 0.01 V – adjust per datasheet.
        return unpack_uint16(raw) * 0.01

    @voltage.setter
    def voltage(self, v: float) -> None:
        raw = pack_uint16(int(v / 0.01))
        self.write_reg(LGA80DReg.VALUE, raw)
