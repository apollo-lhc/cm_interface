import contextlib
import io
import unittest

from cm_interface import si5395_checks as checks
from cm_interface.device.si5395 import Clock


class FakeUART:
    """Answers reads from a register dict (missing registers read 0x00)."""

    def __init__(self, regs):
        self.regs = regs
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        fields = self.writes[-1].decode("ascii").split()
        if fields[0] == "w":
            return b"c\n"
        addr = (int(fields[3], 16) << 8) | int(fields[4], 16)
        data = [self.regs.get(addr + k, 0) for k in range(int(fields[5], 16))]
        return ("d " + " ".join("%02X" % b for b in data) + "\n").encode("ascii")


# Board 3002 R0A, 2026-10-14: free run from XAXB, all inputs and monitors off.
FREE_RUN = {
    0x000C: 0x00, 0x000D: 0x00, 0x000E: 0x22, 0x000F: 0xD0,
    0x0011: 0x1F, 0x0012: 0xF0, 0x0013: 0x22, 0x0014: 0xF2,
    0x002C: 0x00, 0x003F: 0x00, 0x009A: 0x00, 0x0102: 0x01, 0x0145: 0x00,
    0x0507: 0x00, 0x052A: 0x01, 0x0535: 0x01, 0x0949: 0x00, 0x00FE: 0x0F,
}

# Locked to IN1 with IN0 and IN1 enabled and all monitors on.
LOCKED_IN1 = {
    0x002C: 0x03, 0x003F: 0x03, 0x009A: 0x02, 0x0102: 0x01,
    0x0507: 0x40, 0x0535: 0x00, 0x0949: 0x03, 0x00FE: 0x0F,
}


def _with(base, changes):
    regs = dict(base)
    regs.update(changes)
    return regs


def _run(func, regs):
    clock = Clock(FakeUART(regs), address=0x10, name="R0A")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = func(clock, checks.read_monitors(clock))
    return ok, out.getvalue()


class FreeRunTest(unittest.TestCase):
    def test_lol_and_hold_are_expected_in_free_run(self):
        ok, text = _run(checks.check_status, FREE_RUN)
        self.assertTrue(ok, text)
        self.assertIn("expected: LOL (free run", text)
        self.assertIn("expected: HOLD (free run", text)
        self.assertIn("FORCE_HOLD=1", text)

    def test_flags_oof_on_disabled_inputs_expected_but_power_up_flags_fail(self):
        ok, text = _run(checks.check_flags, FREE_RUN)
        self.assertFalse(ok)
        self.assertIn("expected: OOF_FLG IN3 (IN3 disabled)", text)
        self.assertIn("expected: LOL_FLG", text)
        self.assertIn("-> SYSINCAL_FLG  (also latched at power-up", text)
        self.assertIn("-> CAL_FLG_PLL", text)

    def test_flags_pass_once_power_up_flags_cleared(self):
        regs = _with(FREE_RUN, {0x0011: 0x00, 0x0014: 0x00})
        ok, text = _run(checks.check_flags, regs)
        self.assertTrue(ok, text)


class LockedTest(unittest.TestCase):
    def test_lol_is_a_problem_when_input_enabled_and_detection_on(self):
        ok, text = _run(checks.check_status, _with(LOCKED_IN1, {0x000E: 0x02}))
        self.assertFalse(ok)
        self.assertIn("-> LOL", text)

    def test_lol_expected_when_detection_off(self):
        regs = _with(LOCKED_IN1, {0x000E: 0x02, 0x009A: 0x00})
        ok, text = _run(checks.check_status, regs)
        self.assertTrue(ok, text)
        self.assertIn("LOL detection off", text)

    def test_hold_is_a_problem_when_not_forced(self):
        ok, text = _run(checks.check_status, _with(LOCKED_IN1, {0x000E: 0x20}))
        self.assertFalse(ok)
        self.assertIn("-> HOLD", text)

    def test_los_on_active_input_fails_on_standby_input_expected(self):
        ok, text = _run(checks.check_status, _with(LOCKED_IN1, {0x000D: 0x03}))
        self.assertFalse(ok)
        self.assertIn("-> LOS IN1", text)
        self.assertIn("expected: LOS IN0 (IN0 not the active input)", text)

    def test_oof_with_detector_off_expected(self):
        regs = _with(LOCKED_IN1, {0x000D: 0x20, 0x003F: 0x01})
        ok, text = _run(checks.check_status, regs)
        self.assertTrue(ok, text)
        self.assertIn("OOF detection off for IN1", text)


if __name__ == "__main__":
    unittest.main()
