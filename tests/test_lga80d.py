import unittest
from unittest.mock import patch

from cm_interface.device.lga80d import LGA80D, LGA80DSnapshot, LGA80DStatus
from cm_interface.errors import RegisterAccessError


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


def _l11(mantissa, exponent=0):
    """Encode a Linear11 value as two little-endian bytes."""
    raw = ((exponent & 0x1F) << 11) | (mantissa & 0x7FF)
    return raw.to_bytes(2, "little")


def _snapshot_bytes():
    """A synthetic 32-byte snapshot in the firmware's snapshot_t layout."""
    raw = bytearray(32)
    raw[0:2] = _l11(12)                         # v_in = 12 V
    raw[2:4] = (0x1000).to_bytes(2, "little")   # v_out = 0x1000 * 2**-13 = 0.5 V
    raw[4:6] = _l11(-3)                         # i_out = -3 A (unloaded output)
    raw[6:8] = _l11(20)                         # i_out_max = 20 A
    raw[8:10] = _l11(25)                        # duty cycle
    raw[10:12] = _l11(45)                       # temperature
    raw[12:14] = b"\xEE\xEE"                    # unused, must be ignored
    raw[14:16] = _l11(500)                      # switching frequency
    raw[16:23] = bytes([1, 2, 3, 4, 5, 6, 7])   # status bytes
    raw[23:32] = b"\xFF" * 9                    # unused
    return bytes(raw)


def _hex_reply(chunk):
    return ("d " + " ".join("%02X" % b for b in chunk) + "\n").encode("ascii")


class SnapshotDecodeTest(unittest.TestCase):
    def test_decode_fields_and_signed_current(self):
        snap = LGA80DSnapshot.from_bytes(_snapshot_bytes())
        self.assertEqual(snap.input_voltage, 12.0)
        self.assertEqual(snap.output_voltage, 0.5)
        self.assertEqual(snap.output_current, -3.0)  # not clamped to zero
        self.assertEqual(snap.max_output_current, 20.0)
        self.assertEqual(snap.duty_cycle, 25.0)
        self.assertEqual(snap.temperature, 45.0)
        self.assertEqual(snap.switching_frequency, 500.0)
        self.assertEqual(
            (snap.vout_status, snap.iout_status, snap.input_status,
             snap.temperature_status, snap.cml_status,
             snap.manufacturer_status, snap.flash_status),
            (1, 2, 3, 4, 5, 6, 7))
        self.assertEqual(snap.raw, _snapshot_bytes())

    def test_rejects_wrong_length(self):
        for length in (0, 31, 33):
            with self.assertRaises(ValueError):
                LGA80DSnapshot.from_bytes(bytes(length))


class SnapshotRecordAndFormatTest(unittest.TestCase):
    def _snap(self, flash_status):
        raw = bytearray(_snapshot_bytes())
        raw[22] = flash_status
        return LGA80DSnapshot.from_bytes(bytes(raw))

    def test_flash_status_tells_stored_record_from_live_data(self):
        self.assertIs(self._snap(0xFF).is_stored_record, False)  # erased: live
        self.assertIs(self._snap(0x00).is_stored_record, True)   # record stored
        self.assertIsNone(self._snap(0x5A).is_stored_record)     # not understood

    def test_str_is_the_formatted_summary(self):
        text = str(self._snap(0xFF))
        self.assertIn("VIN", text)
        self.assertIn("-3 A", text)  # signed IOUT is shown
        self.assertIn("flash status             0xFF (empty, live data)", text)
        self.assertTrue(text.splitlines()[-1].startswith("raw: "))
        self.assertIn("stored fault record", str(self._snap(0x00)))
        self.assertIn("unknown meaning", str(self._snap(0x5A)))

    def test_format_lines_indent(self):
        lines = self._snap(0xFF).format_lines(indent="  ")
        self.assertTrue(all(line.startswith("  ") for line in lines))


class ReadSnapshotTest(unittest.TestCase):
    def _replies(self, raw):
        return [b"c\n"] + [_hex_reply(raw[i:i + 4]) for i in range(0, 32, 4)]

    def test_wire_sequence_page1_of_supply_index_3(self):
        raw = _snapshot_bytes()
        uart = FakeUART(*self._replies(raw))
        lga = LGA80D(uart, address=0x43, supply_name="F2VCCINT1")

        snap = lga.read_snapshot(page=1)

        self.assertEqual(uart.writes[0], b"w SN 3 1 0 01\n")
        self.assertEqual(
            uart.writes[1:],
            [b"r SN 3 1 %X 4\n" % off for off in range(0, 32, 4)])
        self.assertEqual(snap.raw, raw)
        self.assertEqual(snap.output_current, -3.0)

    def test_page_must_be_0_or_1_and_nothing_is_sent(self):
        uart = FakeUART()
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")
        with self.assertRaises(ValueError):
            lga.read_snapshot(page=2)
        self.assertEqual(uart.writes, [])

    def test_capture_failure_raises_and_sends_no_reads(self):
        uart = FakeUART(b"e SN capture failed\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")
        with self.assertRaises(RegisterAccessError) as ctx:
            lga.read_snapshot()
        self.assertIn("capture failed", str(ctx.exception))
        self.assertEqual(len(uart.writes), 1)

    def test_old_firmware_without_sn_raises(self):
        uart = FakeUART(b"e invalid device type\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")
        with self.assertRaises(RegisterAccessError) as ctx:
            lga.read_snapshot()
        self.assertIn("invalid device type", str(ctx.exception))

    def test_cache_lost_between_capture_and_read_raises(self):
        # another client captured a different supply/page: first read errors
        uart = FakeUART(b"c\n", b"e no SN capture for dev/page\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")
        with self.assertRaises(RegisterAccessError) as ctx:
            lga.read_snapshot()
        self.assertIn("no SN capture", str(ctx.exception))
        self.assertIn("0x00", str(ctx.exception))

    def test_short_read_response_raises(self):
        uart = FakeUART(b"c\n", b"d 01 02\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")
        with self.assertRaises(RegisterAccessError):
            lga.read_snapshot()

    def test_unexpected_write_response_raises(self):
        uart = FakeUART(b"d 00\n")
        lga = LGA80D(uart, address=0x40, supply_name="F1VCCINT1")
        with self.assertRaises(RegisterAccessError):
            lga.read_snapshot()


if __name__ == "__main__":
    unittest.main()
