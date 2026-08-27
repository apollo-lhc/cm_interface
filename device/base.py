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
    def _encode_command(self, reg: int, write: bool, payload: bytes = b"",
                        read_size: int = 1) -> bytes:
        ...

    @abstractmethod
    def _decode_response(self, raw: bytes) -> bytes:
        ...

    @staticmethod
    def _encode_ascii_command(device_type: str, device_number: int, reg: int,
                              write: bool, payload: bytes = b"",
                              read_size: int = 1) -> bytes:
        """Encode the line protocol implemented by MCU ``ProgComTask``."""
        if not 0 <= device_number <= 0xFF:
            raise ValueError(f"device number out of range: {device_number}")
        if not 0 <= reg <= 0xFFFF:
            raise ValueError(f"register out of range: {reg}")
        if len(payload) > 4:
            raise ValueError("MCU protocol accepts at most four data bytes")
        if not 1 <= read_size <= 4:
            raise ValueError("MCU protocol read size must be between 1 and 4 bytes")

        fields = [
            "w" if write else "r",
            device_type,
            f"{device_number:X}",
            f"{(reg >> 8) & 0xFF:X}",
            f"{reg & 0xFF:X}",
        ]
        if write:
            fields.extend(f"{value:02X}" for value in payload)
        else:
            fields.append(f"{read_size:X}")
        return (" ".join(fields) + "\n").encode("ascii")

    @staticmethod
    def _decode_ascii_response(raw: bytes) -> bytes:
        """Decode a successful ``d XX [XX ...]`` read response."""
        try:
            line = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("MCU response is not ASCII") from exc

        if line.startswith("e "):
            raise ValueError(f"MCU error: {line[2:]}")
        fields = line.split()
        if len(fields) < 2 or len(fields) > 5 or fields[0] != "d":
            raise ValueError(f"invalid MCU read response: {line!r}")
        values = []
        for field in fields[1:]:
            try:
                value = int(field, 16)
            except ValueError as exc:
                raise ValueError(f"invalid MCU data byte: {field!r}") from exc
            if not 0 <= value <= 0xFF:
                raise ValueError(f"MCU data byte out of range: {field!r}")
            values.append(value)
        return bytes(values)

    @staticmethod
    def _check_ascii_write_response(raw: bytes) -> None:
        """Validate the MCU's acknowledgement for a write command."""
        try:
            line = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("MCU response is not ASCII") from exc
        if line.startswith("e "):
            raise ValueError(f"MCU error: {line[2:]}")
        if line != "c":
            raise ValueError(f"invalid MCU write response: {line!r}")

    def read_reg(self, reg: int, size: int = 1) -> bytes:
        """Read one to four bytes in a single device transaction."""
        if not 1 <= size <= 4:
            raise ValueError("read size must be between 1 and 4 bytes")
        if reg + size - 1 > 0xFFFF:
            raise ValueError("read extends beyond register 0xFFFF")

        cmd = self._encode_command(reg, write=False, read_size=size)
        self.uart.write(cmd)
        resp = self.uart.readline()
        try:
            value = self._decode_response(resp)
        except Exception as exc:
            raise RegisterAccessError(f"Read register 0x{reg:X} failed") from exc
        if len(value) != size:
            raise RegisterAccessError(
                f"Read register 0x{reg:X} returned {len(value)} bytes; expected {size}"
            )
        return value

    def write_reg(self, reg: int, data: bytes) -> None:
        cmd = self._encode_command(reg, write=True, payload=data)
        self.uart.write(cmd)
        resp = self.uart.readline()
        try:
            self._check_ascii_write_response(resp)
        except Exception as exc:
            raise RegisterAccessError(f"Write register 0x{reg:X} failed") from exc
