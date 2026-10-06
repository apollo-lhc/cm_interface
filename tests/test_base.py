import unittest

from cm_interface.device.base import Device


class FakeUART:
    """Answers every read with zero bytes of the requested size."""

    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        fields = self.writes[-1].decode("ascii").split()
        if fields[0] == "w":
            return b"c\n"
        return ("d " + " ".join(["00"] * int(fields[-1], 16)) + "\n").encode("ascii")


class AsciiDevice(Device):
    def _encode_command(self, reg, write, payload=b"", read_size=1):
        return self._encode_ascii_command("XX", 0, reg, write, payload, read_size)

    def _decode_response(self, raw):
        return self._decode_ascii_response(raw)


class PageBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.uart = FakeUART()
        self.dev = AsciiDevice(self.uart, address=0)

    def test_read_reg_ending_exactly_at_page_end_is_allowed(self):
        self.assertEqual(len(self.dev.read_reg(0x00FC, 4)), 4)

    def test_read_reg_crossing_page_is_rejected_without_traffic(self):
        for reg, size in ((0x00FD, 4), (0x01FF, 2), (0x00FF, 4)):
            with self.assertRaises(ValueError):
                self.dev.read_reg(reg, size)
        self.assertEqual(self.uart.writes, [])

    def test_write_reg_crossing_page_is_rejected_without_traffic(self):
        with self.assertRaises(ValueError):
            self.dev.write_reg(0x01FE, b"\x01\x02\x03")
        self.assertEqual(self.uart.writes, [])

    def test_write_reg_ending_exactly_at_page_end_is_allowed(self):
        self.dev.write_reg(0x01FE, b"\x01\x02")
        self.assertEqual(len(self.uart.writes), 1)

    def test_read_block_splits_at_page_boundary(self):
        data = self.dev.read_block(0x00FE, 6)
        self.assertEqual(len(data), 6)
        # (page, offset, size) of each transaction: 2 bytes up to the page
        # end, then 4 from the start of the next page.
        sent = [tuple(w.decode("ascii").split()[3:5] + [w.decode("ascii").split()[5]])
                for w in self.uart.writes]
        self.assertEqual(sent, [("0", "FE", "2"), ("1", "0", "4")])

    def test_read_block_unaligned_start_never_crosses_page(self):
        self.dev.read_block(0x00FF, 10)
        for w in self.uart.writes:
            _, _, _, page, offset, size = w.decode("ascii").split()
            self.assertLessEqual(int(offset, 16) + int(size, 16), 0x100)


if __name__ == "__main__":
    unittest.main()
