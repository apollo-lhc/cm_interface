import contextlib
import fcntl
import threading
import time

import serial
from typing import Optional, TextIO
from .compat import spaced_hex
from .errors import UARTError, UARTLockTimeout

class UART:
    """Thin wrapper around pySerial for the default /dev/ttyUL4 port.

    The UART connection is opened lazily – if the serial device is not present
    during development, the constructor does not raise an error.  The first
    call to :meth:`write` or :meth:`read` will attempt to open the port.

    Several processes (the ``cm-*`` tools, embedding C++ programs) may share
    the port. :meth:`lock` serializes them with an advisory ``flock`` on the
    tty, so it only excludes clients that take the same lock.
    """
    DEFAULT_BAUD = 115_200
    DEFAULT_TIMEOUT = 5.0  # seconds
    DEFAULT_LOCK_TIMEOUT = 10.0  # seconds
    _LOCK_POLL_S = 0.01

    def __init__(self, dev_path: str = "/dev/ttyUL4",
                 baud: int = DEFAULT_BAUD,
                 timeout: float = DEFAULT_TIMEOUT,
                 debug: Optional[TextIO] = None,
                 lock_timeout: float = DEFAULT_LOCK_TIMEOUT):
        """
        Parameters
        ----------
        dev_path : str
            Serial device path (default: "/dev/ttyUL4")
        baud : int
            Baud rate (default: 115200)
        timeout : float
            Read timeout in seconds (default: 5.0)
        debug : TextIO, optional
            Debug stream for logging UART transactions.
            Use sys.stdout, sys.stderr, or an open file object.
            If None (default), debug logging is disabled.
        lock_timeout : float
            Seconds :meth:`lock` waits for another client before raising
            :class:`UARTLockTimeout` (default: 10.0)
        """
        self.dev_path = dev_path
        self.baud = baud
        self.timeout = timeout
        self.lock_timeout = lock_timeout
        self._ser = None  # lazy connection
        self.debug = debug
        # flock belongs to the open file description, so threads sharing this
        # object's fd do not exclude each other; the RLock covers that.
        self._thread_lock = threading.RLock()
        self._lock_depth = 0
        try:
            self._ser = serial.Serial(dev_path, baudrate=baud,
                                      timeout=timeout)
        except serial.SerialException:
            # Device may be absent during development; defer error until first I/O.
            self._ser = None

    def _ensure_open(self):
        if self._ser is None:
            self._ser = serial.Serial(self.dev_path, baudrate=self.baud,
                                      timeout=self.timeout)
        return self._ser

    @contextlib.contextmanager
    def lock(self):
        """Hold exclusive use of the port for a sequence of transactions.

        Reentrant: nested ``with uart.lock():`` blocks in the same thread
        keep the lock until the outermost one exits. On the outermost
        acquire the input buffer is discarded, so a reply that a previous
        holder gave up on (read timeout) is not taken as ours.

        Raises :class:`UARTLockTimeout` if another client holds the port for
        longer than ``lock_timeout``.
        """
        with self._thread_lock:
            if self._lock_depth == 0:
                ser = self._ensure_open()
                self._acquire_flock(ser.fileno())
                try:
                    ser.reset_input_buffer()
                except BaseException:
                    fcntl.flock(ser.fileno(), fcntl.LOCK_UN)
                    raise
            self._lock_depth += 1
            try:
                yield self
            finally:
                self._lock_depth -= 1
                if self._lock_depth == 0:
                    fcntl.flock(self._ser.fileno(), fcntl.LOCK_UN)

    def _acquire_flock(self, fd: int) -> None:
        # Python 3.6's flock has no timeout; poll the non-blocking form.
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except (BlockingIOError, InterruptedError):
                if time.monotonic() >= deadline:
                    raise UARTLockTimeout(
                        f"{self.dev_path} held by another client for more "
                        f"than {self.lock_timeout} s"
                    )
                time.sleep(self._LOCK_POLL_S)

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
                rendered = f"[hex] {spaced_hex(data)}"

            if direction in ("TX", "RX"):
                self.debug.write(f"UART {direction} ({len(data)}): {rendered}\n")
            if hasattr(self.debug, 'flush'):
                self.debug.flush()

    def write(self, data: bytes) -> None:
        ser = self._ensure_open()
        self._log("TX", data)
        ser.write(data)

    def read(self, size: int) -> bytes:
        chunk = self._ensure_open().read(size)
        self._log("RX", chunk, size)
        if len(chunk) != size:
            raise UARTError(f"Read timeout: expected {size} bytes, got {len(chunk)}")
        return chunk

    def readline(self) -> bytes:
        """Read one newline-terminated response from the MCU."""
        line = self._ensure_open().read_until(b"\n")
        self._log("RX", line)
        if not line.endswith(b"\n"):
            raise UARTError("Read timeout waiting for newline-terminated response")
        return line

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
