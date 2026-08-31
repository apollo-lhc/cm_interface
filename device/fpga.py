"""Raw access to the generic I2C endpoint implemented by an FPGA bitfile."""

from dataclasses import dataclass
from typing import Optional, Union

from .base import Device
from ..errors import CMError, FPGAInterfaceUnavailable, RegisterAccessError


@dataclass(frozen=True)
class FPGAProbeResult:
    """Result of attempting to read a caller-selected, safe probe register."""

    available: bool
    reason: Optional[str] = None

    def __bool__(self) -> bool:
        return self.available


class FPGA(Device):
    """One FPGA's unprofiled, byte-addressed generic register interface.

    The object intentionally assigns no meaning, byte order, or access policy
    to registers. Reads return bytes in wire order. Writes accept bytes in wire
    order; an integer may also be supplied when ``size`` is explicit, in which
    case it is encoded little-endian for compatibility with the current FPGA
    endpoints.
    """

    DEVICE_TYPE = "FP"
    MAX_REGISTER = 0xFF

    def __init__(self, uart, fpga_number: int, name: Optional[str] = None):
        if fpga_number not in (0, 1):
            raise ValueError("FPGA number must be 0 (F1) or 1 (F2)")
        super().__init__(uart, address=fpga_number)
        self.fpga_number = fpga_number
        self.name = name or f"F{fpga_number + 1}"

    @classmethod
    def _validate_transfer(cls, reg: int, size: int) -> None:
        if not isinstance(reg, int) or not 0 <= reg <= cls.MAX_REGISTER:
            raise ValueError(f"FPGA register out of range: {reg!r}")
        if not 1 <= size <= 4:
            raise ValueError("FPGA transfer size must be between 1 and 4 bytes")
        if reg + size - 1 > cls.MAX_REGISTER:
            raise ValueError("FPGA transfer extends beyond register 0xFF")

    def _encode_command(self, reg: int, write: bool, payload: bytes = b"",
                        read_size: int = 1) -> bytes:
        size = len(payload) if write else read_size
        self._validate_transfer(reg, size)
        return self._encode_ascii_command(
            self.DEVICE_TYPE, self.fpga_number, reg, write, payload, read_size
        )

    def _decode_response(self, raw: bytes) -> bytes:
        return self._decode_ascii_response(raw)

    @staticmethod
    def _is_unavailable(exc: BaseException) -> bool:
        """Recognize the NACK spellings used by ProgCom/I2C error reports."""
        current: Optional[BaseException] = exc
        while current is not None:
            message = str(current).lower()
            if any(token in message for token in ("nack", "no ack", "no_ack")):
                return True
            current = current.__cause__
        return False

    def read_reg(self, reg: int, size: int = 1) -> bytes:
        self._validate_transfer(reg, size)
        try:
            return super().read_reg(reg, size=size)
        except RegisterAccessError as exc:
            if self._is_unavailable(exc):
                raise FPGAInterfaceUnavailable(
                    f"{self.name} generic interface is unavailable"
                ) from exc
            raise

    def read_block(self, reg: int, length: int) -> bytes:
        if length < 0:
            raise ValueError("block length cannot be negative")
        if length == 0:
            return b""
        if not isinstance(reg, int) or not 0 <= reg <= self.MAX_REGISTER:
            raise ValueError(f"FPGA register out of range: {reg!r}")
        if reg + length - 1 > self.MAX_REGISTER:
            raise ValueError("FPGA block read extends beyond register 0xFF")
        return super().read_block(reg, length)

    def write_reg(self, reg: int,
                  data: Union[bytes, bytearray, memoryview, int],
                  size: Optional[int] = None) -> None:
        if isinstance(data, int):
            if size is None:
                raise ValueError("size is required when writing an integer")
            self._validate_transfer(reg, size)
            try:
                payload = data.to_bytes(size, "little", signed=False)
            except OverflowError as exc:
                raise ValueError(f"value does not fit in {size} bytes") from exc
        else:
            payload = bytes(data)
            if size is not None and size != len(payload):
                raise ValueError("size does not match the byte payload length")
            self._validate_transfer(reg, len(payload))

        try:
            super().write_reg(reg, payload)
        except RegisterAccessError as exc:
            if self._is_unavailable(exc):
                raise FPGAInterfaceUnavailable(
                    f"{self.name} generic interface is unavailable"
                ) from exc
            raise

    def probe(self, reg: int = 0x00) -> FPGAProbeResult:
        """Try a register that the caller knows is safe to read.

        Register 0 is the default because it is a side-effect-free scratch
        register in the currently deployed frequency-test bitfile. For an
        unknown bitfile, callers should explicitly choose a known-safe address.
        """
        try:
            self.read_reg(reg)
        except CMError as exc:
            return FPGAProbeResult(False, str(exc))
        return FPGAProbeResult(True)
