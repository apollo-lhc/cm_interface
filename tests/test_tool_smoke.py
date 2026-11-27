"""Run every installed cm-* tool's main() end to end against a fake board.

The other tool tests mostly exercise decoding helpers; nothing else runs the
main() that wires them together, which is how a helper's signature change
reached hardware with a caller left behind (cm-ff-dump: state_lines() given
the power text instead of the (text, state) pair). This suite only asks that
each tool finish with an exit status instead of a traceback, with the MCU
reporting power on, power off, and with nothing on the UART answering.

The fake speaks the MCU line protocol under a real Registry, so the tools
take the same code paths as on hardware. The MCU pages hold a valid System
header and the chosen power state; every other device reads as zeros and
accepts writes.
"""
import contextlib
import importlib
import io
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

from cm_interface import registry as registry_module
from cm_interface.device.mcu import (McuCapability, McuPage, PowerFsmState, PowerReg,
                                     SystemReg)

HW_REV = 3
ALL_CAPS = (McuCapability.SYSTEM | McuCapability.POWER | McuCapability.ALARMS
            | McuCapability.ADC | McuCapability.PERSISTENT_LOG
            | McuCapability.CONTROLS | McuCapability.RUNTIME
            | McuCapability.CONFIG | McuCapability.CONFIG_WRITE)

SPEC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "packaging", "cm_interface.spec")

# One representative invocation per installed tool. Tools that write ask for
# no confirmation (--yes) so input() is never reached; the fake accepts writes.
TOOLS = {
    "clock_checks": [],
    "clock_dump": ["--all"],
    "ff_cdr": ["--all"],
    "ff_dump": ["--all"],
    "ff_rx_eq": ["--all"],
    "ff_status": ["--all"],
    "is_cm_healthy": [],
    "is_cm_power_on": [],
    "is_fpga_programmed": [],
    "is_rtc_set": [],
    "lga80d_checks": [],
    "lga80d_clear_faults": ["--all", "--yes"],
    "lga80d_dump": ["--all"],
    "lga80d_snapshot_reset": ["--all", "--yes"],
    "lga80d_status": ["--all"],
    "mcu_control": ["--send-control", "CLEAR_ALARM_LATCHES", "--yes"],
    "mcu_dump": [],
    "mcu_set_rtc": ["--dry-run"],
    "wait_cm_power_on": ["--timeout", "0"],
}


class FakeBoardUART:
    """Answers the MCU line protocol: 'r TT N HH LL S' and 'w TT N HH LL D..'.
    MC reads come from per-page memory; other devices read as zeros. With
    dead=True every command gets an MCU error, as if nothing answered."""

    def __init__(self, power_state, dead=False):
        self.dead = dead
        self.pages = {int(page): bytearray(256) for page in McuPage}
        self._put(McuPage.SYSTEM, SystemReg.MAGIC, b"CMCU")
        self._put(McuPage.SYSTEM, SystemReg.MAP_MAJOR, bytes((1, 1)))
        self._put(McuPage.SYSTEM, SystemReg.HARDWARE_REVISION, bytes((HW_REV,)))
        self._put(McuPage.SYSTEM, SystemReg.CAPABILITIES, int(ALL_CAPS).to_bytes(4, "little"))
        self._put(McuPage.SYSTEM, SystemReg.BOARD_ID, (3011).to_bytes(4, "little"))
        self._put(McuPage.SYSTEM, SystemReg.FF_USER_MASK, (0xFFFFF).to_bytes(4, "little"))
        self._put(McuPage.SYSTEM, SystemReg.FF_PRESENT_MASK, (0xFFFFF).to_bytes(4, "little"))
        self._put(McuPage.POWER, PowerReg.FSM_STATE, bytes((int(power_state),)))
        self._last = None

    def _put(self, page, offset, data):
        self.pages[int(page)][int(offset):int(offset) + len(data)] = data

    def write(self, data):
        self._last = data.decode("ascii").split()

    def readline(self):
        fields, self._last = self._last, None
        if self.dead or fields is None:
            return b"e no response\n"
        if fields[0] == "w":
            return b"c\n"
        size = int(fields[5], 16)
        if fields[1] == "MC":
            page, offset = int(fields[3], 16), int(fields[4], 16)
            data = self.pages.get(page, bytearray(256))[offset:offset + size]
        else:
            data = bytes(size)
        return ("d " + " ".join("%02X" % b for b in data) + "\n").encode("ascii")


@contextlib.contextmanager
def fake_board(uart):
    """A fresh Registry singleton on uart, with no real sleeps."""
    saved = registry_module.Registry._instance
    registry_module.Registry._instance = None
    try:
        with mock.patch.object(registry_module, "UART", lambda **kwargs: uart), \
                mock.patch("time.sleep"):
            yield
    finally:
        registry_module.Registry._instance = saved


def run_tool(name, argv, uart):
    """Run tool name's main() with argv; return (exit status, output)."""
    module = importlib.import_module("cm_interface.tools." + name)
    out = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp:
        # wait_cm_power_on refuses a dev path that does not exist.
        dev_path = os.path.join(tmp, "ttyFAKE")
        open(dev_path, "w").close()
        with fake_board(uart), \
                mock.patch.object(sys, "argv", ["cm-" + name.replace("_", "-"),
                                                "--dev-path", dev_path] + argv), \
                mock.patch("builtins.input", side_effect=AssertionError("prompted")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            status = module.main()
    return status, out.getvalue()


class ToolSmokeTest(unittest.TestCase):
    def _run_all(self, make_uart):
        for name, argv in sorted(TOOLS.items()):
            with self.subTest(tool=name):
                try:
                    status, out = run_tool(name, argv, make_uart())
                except SystemExit as exc:
                    self.fail("%s exited through SystemExit(%r)" % (name, exc.code))
                except Exception as exc:
                    raise AssertionError("%s raised %s: %s"
                                         % (name, type(exc).__name__, exc)) from exc
                self.assertIsInstance(status, int, "%s returned %r\n%s" % (name, status, out))
                self.assertNotIn("Traceback", out)

    def test_power_on(self):
        self._run_all(lambda: FakeBoardUART(PowerFsmState.POWER_ON))

    def test_power_off(self):
        self._run_all(lambda: FakeBoardUART(PowerFsmState.POWER_OFF))

    def test_nothing_answers(self):
        self._run_all(lambda: FakeBoardUART(PowerFsmState.POWER_ON, dead=True))

    def test_ff_dump_header_with_power_on(self):
        status, out = run_tool("ff_dump", ["--all"], FakeBoardUART(PowerFsmState.POWER_ON))
        self.assertIn("  power:     POWER_ON", out)
        self.assertIn("  fpga done: F1 done, F2 done", out)  # pins read 0 = done (active low)

    def test_every_installed_tool_is_covered(self):
        with open(SPEC, encoding="utf-8") as fh:
            spec = fh.read()
        match = re.search(r"for tool in (.*?); do", spec, re.S)
        installed = set(match.group(1).replace("\\", " ").split())
        self.assertEqual(installed, set(TOOLS),
                         "update TOOLS to match the launchers in %s" % SPEC)


if __name__ == "__main__":
    unittest.main()
