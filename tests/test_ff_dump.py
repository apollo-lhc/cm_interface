import unittest

from cm_interface.device.firefly import Firefly4, FireflyRx, FireflyTx
from cm_interface.tools import ff_dump


class FakeUART:
    """Answers FF reads from a byte map (missing bytes read 0x00); reads at
    addresses in fail_at answer with an MCU error."""

    def __init__(self, regs, fail_at=()):
        self.regs = regs
        self.fail_at = set(fail_at)
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        fields = self.writes[-1].decode("ascii").split()
        if fields[0] == "w":
            return b"c\n"
        addr = (int(fields[3], 16) << 8) | int(fields[4], 16)
        if addr in self.fail_at:
            return b"e FF read failed\n"
        data = [self.regs.get(addr + k, 0) for k in range(int(fields[5], 16))]
        return ("d " + " ".join("%02X" % b for b in data) + "\n").encode("ascii")


def ascii_at(regs, addr, text):
    for i, ch in enumerate(text.encode("ascii")):
        regs[addr + i] = ch


def x12_tx_regs():
    regs = {}
    ascii_at(regs, 0xAB, "B0412500021111")   # 25g part
    ascii_at(regs, 0xBD, "SN1234")
    regs[0x16] = 0xFE                         # -2 C
    regs[0x1A], regs[0x1B] = 0x80, 0xE8       # 0x80E8 * 100 uV = 3.3000 V
    regs[0x06] = 0x80                         # Tx LOS summary
    regs[0x07], regs[0x08] = 0x08, 0x01       # LOS ch 1 and ch 12
    regs[0x4A], regs[0x4B] = 0xFF, 0xFF       # CDR enable: reserved nibble set, ch 1-12
    return regs


def setUpModule():
    # No real sleeps in the software suite; record them instead.
    ff_dump.time = FakeTime()


def tearDownModule():
    ff_dump.time = __import__("time")


class FakeTime:
    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def strftime(self, fmt):
        return "2026-01-01 00:00:00"


class ReadRawTest(unittest.TestCase):
    def test_reads_lower_and_upper_page_in_four_byte_transactions(self):
        uart = FakeUART(x12_tx_regs())
        ff_dump.time.sleeps = []
        raw, error = ff_dump.read_raw(FireflyTx(uart, address=0x20, location="F1_1"))
        self.assertIsNone(error)
        # 64 data reads plus one page-select read before the upper page.
        self.assertEqual(len(uart.writes), 65)
        self.assertTrue(all(w.startswith(b"r FF 0 0 ") for w in uart.writes))
        self.assertEqual(uart.writes[32], b"r FF 0 0 80 1\n")
        self.assertEqual(uart.writes[33], b"r FF 0 0 80 4\n")
        self.assertEqual(ff_dump.time.sleeps, [ff_dump.PAGE_SELECT_WAIT_S])
        self.assertEqual(raw[0x16], 0xFE)
        self.assertEqual(raw[0xAB], ord("B"))

    def test_failed_chunk_is_marked_and_reading_continues(self):
        uart = FakeUART(x12_tx_regs(), fail_at=[0x14])
        raw, error = ff_dump.read_raw(FireflyTx(uart, address=0x20, location="F1_1"))
        self.assertIsNotNone(error)
        self.assertEqual(raw[0x14:0x18], [None] * 4)
        self.assertEqual(raw[0xAB], ord("B"))
        self.assertIn("10: 00 00 00 00 ?? ?? ?? ??  00 00 80 E8",
                      "\n".join(ff_dump.hex_lines(raw)))


class DecodeTest(unittest.TestCase):
    def decode(self, cls, kind, regs, fail_at=()):
        ff = cls(FakeUART(regs, fail_at), address=0x20, location="F1_1")
        raw, _ = ff_dump.read_raw(ff)
        variant, lines = ff_dump.decoded_lines(ff, kind, raw)
        return variant, "\n".join(lines)

    def test_x12_tx_fields(self):
        variant, text = self.decode(FireflyTx, "Tx", x12_tx_regs())
        self.assertEqual(variant, "x12 Tx, 25g")
        self.assertIn("B0412500021111", text)
        self.assertIn("SN1234", text)
        self.assertIn("-2 C", text)
        self.assertIn("3.3000 V", text)
        self.assertIn("Tx LOS", text)
        self.assertRegex(text, r"LOS latch\s+0x0801  ch 1,12")
        # The reserved high nibble of byte 74 is shown raw but not as channels.
        self.assertRegex(text, r"CDR enable\s+0xFFFF  ch 1,2,3,4,5,6,7,8,9,10,11,12")

    def test_x12_rx_eq_nibbles(self):
        regs = {}
        ascii_at(regs, 0xAB, "B0412500021111")
        regs[0x3E] = 0x40                     # ch 12 = 4 (high nibble of first byte)
        regs[0x43] = 0x07                     # ch 1 = 7 (low nibble of last byte)
        _, text = self.decode(FireflyRx, "Rx", regs)
        self.assertIn("1:7(612 mV)", text)
        self.assertIn("12:4(384 mV)", text)

    def test_cernb_gets_no_control_registers(self):
        regs = {}
        ascii_at(regs, 0xAB, "CERNB-R12")
        variant, text = self.decode(FireflyRx, "Rx", regs)
        self.assertEqual(variant, "x12 Rx, cernb")
        self.assertIn("LOS latch", text)
        self.assertNotIn("CDR", text)
        self.assertNotIn("Rx amplitude", text)

    def test_x4_fields(self):
        regs = {}
        ascii_at(regs, 0xA8, "B0425040011201")
        ascii_at(regs, 0x94, "Samtec")
        regs[0x03] = 0x21                     # Tx LOS ch 2, Rx LOS ch 1
        regs[0x62] = 0xF0                     # CDR enable Tx only
        variant, text = self.decode(Firefly4, "4", regs)
        self.assertEqual(variant, "x4")
        self.assertIn("Samtec", text)
        self.assertNotIn("serial", text)
        self.assertRegex(text, r"LOS latch\s+0x21  Tx ch 2; Rx ch 1")
        self.assertRegex(text, r"CDR enable\s+0xF0  Tx ch 1,2,3,4; Rx none")

    def test_x4_rx_power_alarms_and_optical_power(self):
        regs = {}
        ascii_at(regs, 0xA8, "B0425040011201")
        regs[0x09] = 0x82                     # Rx1 high alarm, Rx2 high warning
        regs[0x0A] = 0x01                     # Rx4 low warning
        regs[0x22], regs[0x23] = 0x27, 0x10   # Rx1 10000 counts = 1000 uW = 0 dBm
        regs[0x24], regs[0x25] = 0x00, 0x64   # Rx2 100 counts = 10 uW = -20 dBm
        _, text = self.decode(Firefly4, "4", regs)
        self.assertRegex(text, r"Rx power alarm\s+0x8201  ch 1 high alarm, "
                               r"ch 2 high warning, ch 4 low warning")
        self.assertRegex(text, r"Rx1 optical power\s+1000\.0 uW \(0\.00 dBm\)")
        self.assertRegex(text, r"Rx2 optical power\s+10\.0 uW \(-20\.00 dBm\)")
        self.assertRegex(text, r"Rx3 optical power\s+0\.0 uW\n")

    def test_x4_vcc18(self):
        regs = {}
        ascii_at(regs, 0xA8, "B0425040011201")
        regs[0x1C], regs[0x1D] = 0x46, 0x50   # 0x4650 * 100 uV = 1.8000 V
        _, text = self.decode(Firefly4, "4", regs)
        self.assertRegex(text, r"Vcc1\.8\s+1\.8000 V")

    def test_unread_bytes_show_not_read(self):
        variant, text = self.decode(FireflyTx, "Tx", x12_tx_regs(), fail_at=[0x14, 0xAC])
        self.assertEqual(variant, "x12 Tx, family unknown (part not read)")
        self.assertRegex(text, r"part\s+not read")
        self.assertRegex(text, r"temperature\s+not read")


class RxRssiTest(unittest.TestCase):
    def rx_regs(self, part):
        regs = {}
        ascii_at(regs, 0xAB, part)
        regs[0x01E4], regs[0x01E5] = 0x27, 0x10   # ch 1 (last pair): 1000 uW = 0 dBm
        regs[0x01CE], regs[0x01CF] = 0x00, 0x64   # ch 12 (first pair): 10 uW = -20 dBm
        return regs

    def test_25g_rx_reads_page_01_and_restores_page_00(self):
        uart = FakeUART(self.rx_regs("B0412500021111"))
        ff_dump.time.sleeps = []
        lines, failed = ff_dump.dump_module(1, "F1_1 Rx", "Rx",
                                            FireflyRx(uart, address=0x21, location="F1_1"))
        text = "\n".join(lines)
        self.assertFalse(failed)
        reads = [w.decode("ascii").split()[3:6] for w in uart.writes]
        # Page 00 select, then page 01 select, the six RSSI reads, page 00 again.
        self.assertEqual(reads[-8:], [["1", "80", "1"], ["1", "CE", "4"], ["1", "D2", "4"],
                                      ["1", "D6", "4"], ["1", "DA", "4"], ["1", "DE", "4"],
                                      ["1", "E2", "4"], ["0", "80", "1"]])
        self.assertEqual(len(ff_dump.time.sleeps), 3)
        self.assertRegex(text, r"Rx1 optical power\s+1000\.0 uW \(0\.00 dBm\)")
        self.assertRegex(text, r"Rx12 optical power\s+10\.0 uW \(-20\.00 dBm\)")
        self.assertIn("raw upper page 01h", text)
        self.assertIn("C0:", text)

    def test_cernb_and_tx_do_not_read_page_01(self):
        for cls, kind, part in ((FireflyRx, "Rx", "CERNB-R12"),
                                (FireflyTx, "Tx", "B0412500021111")):
            uart = FakeUART(self.rx_regs(part))
            lines, _ = ff_dump.dump_module(0, "F1_1 " + kind, kind,
                                           cls(uart, address=0x20, location="F1_1"))
            self.assertFalse(any(w.split()[3] == b"1" for w in uart.writes))
            self.assertNotIn("optical power", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
