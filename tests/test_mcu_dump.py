import contextlib
import io
import os
import tempfile
import types
import unittest

from cm_interface import mcu_dump
from cm_interface.device.mcu import MCU, McuCapability, McuPage, SystemReg
from cm_interface.lga80d_dump import board_info

ALL_CAPS = (McuCapability.SYSTEM | McuCapability.POWER | McuCapability.ALARMS
            | McuCapability.ADC | McuCapability.PERSISTENT_LOG
            | McuCapability.CONTROLS | McuCapability.RUNTIME
            | McuCapability.CONFIG | McuCapability.CONFIG_WRITE)


class MemoryMCU(MCU):
    """Zero-filled pages with a valid System header, map 1.1."""

    def __init__(self, capabilities=ALL_CAPS, board_id=3019):
        self.pages = {page: bytearray(256) for page in McuPage}
        self.writes = []
        self.put(McuPage.SYSTEM, SystemReg.MAGIC, b"CMCU")
        self.put(McuPage.SYSTEM, SystemReg.MAP_MAJOR, bytes((1, 1)))
        self.put(McuPage.SYSTEM, SystemReg.CAPABILITIES,
                 int(capabilities).to_bytes(4, "little"))
        self.put(McuPage.SYSTEM, SystemReg.BOARD_ID, board_id.to_bytes(4, "little"))

    def put(self, page, offset, data):
        self.pages[int(page)][int(offset):int(offset) + len(data)] = data

    def read_reg(self, reg, size=1):
        page, offset = divmod(int(reg), 256)
        return bytes(self.pages[page][offset:offset + size])

    def write_reg(self, reg, data):
        self.writes.append((int(reg), bytes(data)))


class FailingMCU(MCU):
    def __init__(self):
        self.writes = []

    def read_reg(self, reg, size=1):
        raise OSError("could not open port")

    def write_reg(self, reg, data):
        self.writes.append((int(reg), bytes(data)))


class FakeRegistry:
    def __init__(self, mcu):
        self.mcu = mcu

    def get_mcu(self):
        return self.mcu


def _dump(mcu, out_dir=None):
    """Run dump_mcu; return (failed, stdout text)."""
    reg = FakeRegistry(mcu)
    args = types.SimpleNamespace(dev_path="/dev/fake", out_dir=out_dir)
    board_lines, board_id = board_info(reg)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        failed = mcu_dump.dump_mcu(reg, args, board_lines, board_id)
    return failed, out.getvalue()


class DumpMcuTest(unittest.TestCase):
    def test_all_sections_and_file(self):
        mcu = MemoryMCU()
        with tempfile.TemporaryDirectory() as out_dir:
            failed, text = _dump(mcu, out_dir)
            path = os.path.join(out_dir, "cm3019_mcu.txt")
            with open(path, encoding="utf-8") as fh:
                written = fh.read()

        self.assertFalse(failed)
        self.assertIn("board id:  3019", text)
        for title in ("System (0x00)", "Power (0x01)", "Alarm (0x02)",
                      "Config (0x05)", "Runtime (0x06)", "ADC sample (0x03)",
                      "Persistent log"):
            self.assertIn(title, written)
        self.assertNotIn("⚠", written)
        # stdout is the file contents plus the 'wrote' line
        self.assertEqual(text, written + "wrote %s\n" % path)

    def test_read_only(self):
        mcu = MemoryMCU()
        _dump(mcu)
        self.assertEqual(mcu.writes, [])

    def test_unadvertised_page_is_not_a_failure(self):
        mcu = MemoryMCU(capabilities=McuCapability.SYSTEM)
        failed, text = _dump(mcu)
        self.assertFalse(failed)
        self.assertIn("not advertised by firmware", text)

    def test_failure_is_reported_not_raised(self):
        mcu = FailingMCU()
        with tempfile.TemporaryDirectory() as out_dir:
            failed, text = _dump(mcu, out_dir)
            self.assertTrue(os.path.exists(os.path.join(out_dir, "cmunknown_mcu.txt")))

        self.assertTrue(failed)
        self.assertIn("board id:  unavailable", text)
        self.assertIn("could not open port", text)
        self.assertIn("(read failures)", text)
        self.assertEqual(mcu.writes, [])


if __name__ == "__main__":
    unittest.main()
