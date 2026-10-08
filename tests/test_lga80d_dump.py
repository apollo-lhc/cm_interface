import unittest

from cm_interface import lga80d_dump
from cm_interface.device.lga80d import LGA80D


class FakeUART:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        return self.responses.pop(0)


def _l11(mantissa, exponent=0):
    return (((exponent & 0x1F) << 11) | (mantissa & 0x7FF)).to_bytes(2, "little")


def _snapshot_bytes():
    raw = bytearray(32)
    raw[0:2] = _l11(12)                         # VIN
    raw[2:4] = (0x1000).to_bytes(2, "little")   # VOUT = 0.5 V
    raw[4:6] = _l11(-3)                         # IOUT = -3 A
    raw[16:23] = bytes([1, 2, 3, 4, 5, 6, 7])   # status bytes
    return bytes(raw)


def _hex_reply(chunk):
    return ("d " + " ".join("%02X" % b for b in chunk) + "\n").encode("ascii")


class SnapshotLinesTest(unittest.TestCase):
    def test_success_lists_decoded_fields_and_raw_bytes(self):
        raw = _snapshot_bytes()
        uart = FakeUART(b"c\n", *[_hex_reply(raw[i:i + 4]) for i in range(0, 32, 4)])
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")

        lines, ok = lga80d_dump.snapshot_lines(lga, 0)
        text = "\n".join(lines)

        self.assertTrue(ok)
        self.assertIn("VIN", text)
        self.assertIn("-3 A", text)  # signed IOUT is shown, not clamped
        self.assertIn("flash status", text)
        self.assertIn(" ".join("%02X" % b for b in raw), text)

    def test_failure_is_reported_not_raised(self):
        uart = FakeUART(b"e invalid device type\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")

        lines, ok = lga80d_dump.snapshot_lines(lga, 0)

        self.assertFalse(ok)
        self.assertIn("READ FAILED", "\n".join(lines))
        self.assertIn("invalid device type", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
