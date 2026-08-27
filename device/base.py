from abc import ABC, abstractmethod
from ..uart import UART
from ..errors import RegisterAccessError

class Device(ABC):
    """Abstract base class for all UART‑reachable devices.

    Sub‑classes provide the low‑level command encoding/decoding required for
    their specific register map.  The base class implements generic ``read_reg``
    and ``write_reg`` helpers that use the subclass‑provided framing methods.
    """
    def __init__(self, uart: UART, address: int):
        self.uart = uart
        self.address = address

    @abstractmethod
    def _encode_command(self, reg: int, write: bool, payload: bytes = b"") -> bytes:
        ...

    @abstractmethod
    def _decode_response(self, raw: bytes) -> bytes:
        ...

    def _expected_response_len(self) -> int:
        # Placeholder – most devices return 4 bytes (addr+reg+crc). Override if needed.
        return 4

    def read_reg(self, reg: int) -> bytes:
        cmd = self._encode_command(reg, write=False)
        self.uart.write(cmd)
        resp = self.uart.read(self._expected_response_len())
        try:
            return self._decode_response(resp)
        except Exception as exc:
            raise RegisterAccessError(f"Read register 0x{reg:X} failed") from exc

    def write_reg(self, reg: int, data: bytes) -> None:
        cmd = self._encode_command(reg, write=True, payload=data)
        self.uart.write(cmd)
        # Many devices do not send ACK; override if needed.
