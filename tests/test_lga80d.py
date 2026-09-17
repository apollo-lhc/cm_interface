import unittest
from unittest.mock import patch

from cm_interface.device.lga80d import LGA80D, LGA80DStatus


class FakeUART:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        return self.responses.pop(0)


def _status(word=0, **overrides):
    fields = dict(word=word, vout=0, iout=0, input=0, temperature=0, cml=0,
                  manufacturer=0)
    fields.update(overrides)
    return LGA80DStatus(**fields)


class HasFaultsTest(unittest.TestCase):
    def test_off_bit_alone_is_not_a_fault(self):
        self.assertFalse(_status(word=1 << 6).has_faults)

    def test_power_good_bit_alone_is_not_a_fault(self):
        self.assertFalse(_status(word=1 << 11).has_faults)

    def test_off_and_power_good_together_are_not_a_fault(self):
        self.assertFalse(_status(word=(1 << 6) | (1 << 11)).has_faults)

    def test_all_zero_is_not_a_fault(self):
        self.assertFalse(_status().has_faults)

    def test_other_status_word_bit_is_a_fault(self):
        self.assertTrue(_status(word=1 << 4).has_faults)  # VOUT_OC_FAULT

    def test_off_bit_plus_another_bit_is_still_a_fault(self):
        self.assertTrue(_status(word=(1 << 6) | (1 << 4)).has_faults)

    def test_nonzero_category_byte_is_a_fault(self):
        for field in ("vout", "iout", "input", "temperature", "cml", "manufacturer"):
            self.assertTrue(_status(**{field: 1}).has_faults, field)


class ReadBlockTest(unittest.TestCase):
    def test_read_block_still_raises(self):
        lga = LGA80D(FakeUART(), address=0x40, supply_name="F1VCCINT1")
        with self.assertRaises(NotImplementedError):
            lga.read_block(0x99, 12)


class StatusWordDecodeTest(unittest.TestCase):
    def test_little_endian_decode(self):
        uart = FakeUART(b"d 34 12\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")
        self.assertEqual(lga.read_status_word(), 0x1234)


class ResetSnapshotTest(unittest.TestCase):
    """Regression tests for the snapshot-reset helper, modeled on the MCU
    firmware's sn_all/snapdump_locked (PowerCommands.c / LocalTasks.c):
    write SNAPSHOT_CONTROL=0x01 (capture), wait, then write
    SNAPSHOT_CONTROL=0x03 (reset), per page."""

    def test_reset_snapshot_wire_sequence_page0(self):
        uart = FakeUART(b"c\n", b"c\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")

        with patch("cm_interface.device.lga80d.time.sleep") as sleep:
            lga.reset_snapshot(page=0)

        self.assertEqual(uart.writes, [
            b"w DC 0 0 F3 01\n",
            b"w DC 0 0 F3 03\n",
        ])
        sleep.assert_called_once()

    def test_reset_snapshot_wire_sequence_page1(self):
        uart = FakeUART(b"c\n", b"c\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")

        with patch("cm_interface.device.lga80d.time.sleep"):
            lga.reset_snapshot(page=1)

        self.assertEqual(uart.writes, [
            b"w DC 0 1 F3 01\n",
            b"w DC 0 1 F3 03\n",
        ])

    def test_reset_all_snapshots_covers_both_pages(self):
        uart = FakeUART(b"c\n", b"c\n", b"c\n", b"c\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")

        with patch("cm_interface.device.lga80d.time.sleep"):
            lga.reset_all_snapshots()

        self.assertEqual(uart.writes, [
            b"w DC 0 0 F3 01\n",
            b"w DC 0 0 F3 03\n",
            b"w DC 0 1 F3 01\n",
            b"w DC 0 1 F3 03\n",
        ])


if __name__ == "__main__":
    unittest.main()
