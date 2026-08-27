import serial
from typing import Optional, TextIO
from .errors import UARTError

class UART:
    """Thin wrapper around pySerial for the fixed /dev/ttySL4 port.

    The UART connection is opened lazily – if the serial device is not present
    during development, the constructor does not raise an error.  The first
    call to :meth:`write` or :meth:`read` will attempt to open the port.
    """
    DEFAULT_BAUD = 115_200
    DEFAULT_TIMEOUT = 5.0  # seconds

    def __init__(self, dev_path: str = "/dev/ttySL4",
                 baud: int = DEFAULT_BAUD,
                 timeout: float = DEFAULT_TIMEOUT,
                 debug: Optional[TextIO] = None):
        """
        Parameters
        ----------
        dev_path : str
            Serial device path (default: "/dev/ttySL4")
        baud : int
            Baud rate (default: 115200)
        timeout : float
            Read timeout in seconds (default: 5.0)
        debug : TextIO, optional
            Debug stream for logging UART transactions.
            Use sys.stdout, sys.stderr, or an open file object.
            If None (default), debug logging is disabled.
        """
        self.dev_path = dev_path
        self.baud = baud
        self.timeout = timeout
        self._ser = None  # lazy connection
        self.debug = debug
        try:
            self._ser = serial.Serial(dev_path, baudrate=baud,
                                      timeout=timeout)
        except serial.SerialException:
            # Device may be absent during development; defer error until first I/O.
            self._ser = None

    def _log(self, direction: str, data: bytes, size: int = None) -> None:
        """Log UART transaction to debug stream."""
        if self.debug is not None:
            try:
                # Keep line terminators visible instead of letting them break the
                # debug record onto a second line.
                rendered = (
                    data.decode("ascii")
                    .encode("unicode_escape")
                    .decode("ascii")
                )
            except UnicodeDecodeError:
                rendered = f"[hex] {data.hex(' ')}"

            if direction in ("TX", "RX"):
                self.debug.write(f"UART {direction} ({len(data)}): {rendered}\n")
            if hasattr(self.debug, 'flush'):
                self.debug.flush()

    def write(self, data: bytes) -> None:
        if self._ser is None:
            self._ser = serial.Serial(self.dev_path, baudrate=self.baud,
                                      timeout=self.timeout)
        self._log("TX", data)
        self._ser.write(data)

    def read(self, size: int) -> bytes:
        if self._ser is None:
            self._ser = serial.Serial(self.dev_path, baudrate=self.baud,
                                      timeout=self.timeout)
        chunk = self._ser.read(size)
        self._log("RX", chunk, size)
        if len(chunk) != size:
            raise UARTError(f"Read timeout: expected {size} bytes, got {len(chunk)}")
        return chunk

    def readline(self) -> bytes:
        """Read one newline-terminated response from the MCU."""
        if self._ser is None:
            self._ser = serial.Serial(self.dev_path, baudrate=self.baud,
                                      timeout=self.timeout)
        line = self._ser.read_until(b"\n")
        self._log("RX", line)
        if not line.endswith(b"\n"):
            raise UARTError("Read timeout waiting for newline-terminated response")
        return line

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
