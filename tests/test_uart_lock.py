"""UART.lock tests. A temporary file stands in for the tty: flock behaves the
same on a regular file, so no serial device is needed."""
import fcntl
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from cm_interface.device.base import Device
from cm_interface.errors import UARTLockTimeout
from cm_interface.uart import UART


class FakeSerial:
    """Minimal pySerial stand-in backed by a real file descriptor."""

    def __init__(self, path, *args, **kwargs):
        self._fd = os.open(path, os.O_RDWR)
        self.resets = 0
        self.writes = []

    def fileno(self):
        return self._fd

    def reset_input_buffer(self):
        self.resets += 1

    def write(self, data):
        self.writes.append(data)

    def read_until(self, terminator):
        return b"d 2A\n"

    def close(self):
        os.close(self._fd)


def _held_elsewhere(path):
    """True if a separate open file description cannot take the lock."""
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


class AsciiDevice(Device):
    def _encode_command(self, reg, write, payload=b"", read_size=1):
        return self._encode_ascii_command("FF", 0, reg, write, payload, read_size)

    def _decode_response(self, raw):
        return self._decode_ascii_response(raw)


class UARTLockTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp()
        os.close(handle)
        patcher = patch("cm_interface.uart.serial.Serial", FakeSerial)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.uart = UART(dev_path=self.path, lock_timeout=0.2)
        self.addCleanup(self.uart.close)
        self.addCleanup(os.unlink, self.path)

    def test_lock_excludes_other_open_file_descriptions(self):
        self.assertFalse(_held_elsewhere(self.path))
        with self.uart.lock():
            self.assertTrue(_held_elsewhere(self.path))
        self.assertFalse(_held_elsewhere(self.path))

    def test_lock_is_reentrant_and_released_by_outermost_exit(self):
        with self.uart.lock():
            with self.uart.lock():
                pass
            self.assertTrue(_held_elsewhere(self.path))
        self.assertFalse(_held_elsewhere(self.path))

    def test_input_buffer_flushed_once_per_outermost_acquire(self):
        with self.uart.lock():
            with self.uart.lock():
                pass
        with self.uart.lock():
            pass
        self.assertEqual(self.uart._ser.resets, 2)

    def test_released_when_body_raises(self):
        with self.assertRaises(RuntimeError):
            with self.uart.lock():
                raise RuntimeError("boom")
        self.assertFalse(_held_elsewhere(self.path))

    def test_times_out_when_another_client_holds_the_port(self):
        fd = os.open(self.path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with self.assertRaises(UARTLockTimeout):
                with self.uart.lock():
                    self.fail("lock body ran while another client held it")
            self.assertEqual(self.uart._lock_depth, 0)
        finally:
            os.close(fd)

    def test_waits_for_release_by_another_client(self):
        fd = os.open(self.path, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        timer = threading.Timer(0.05, os.close, args=(fd,))
        timer.start()
        try:
            with self.uart.lock():
                pass
        finally:
            timer.join()

    def test_threads_sharing_one_uart_are_serialized(self):
        inside = []
        overlap = []

        def worker():
            for _ in range(50):
                with self.uart.lock():
                    inside.append(1)
                    if len(inside) > 1:
                        overlap.append(1)
                    inside.pop()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(overlap, [])

    def test_device_transaction_holds_lock(self):
        dev = AsciiDevice(self.uart, address=0)
        held = []
        real_write = self.uart.write

        def write(data):
            held.append(_held_elsewhere(self.path))
            real_write(data)

        self.uart.write = write
        self.assertEqual(dev.read_reg(0x02), b"\x2A")
        self.assertEqual(held, [True])
        self.assertFalse(_held_elsewhere(self.path))


if __name__ == "__main__":
    unittest.main()
