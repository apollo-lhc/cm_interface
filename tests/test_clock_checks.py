import argparse
import contextlib
import io
import unittest

from cm_interface import clock_checks as checks
from cm_interface import clock_dump
from cm_interface.device.si5341 import Si5341Clock
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
        self.assertIn("-> SYSINCAL_FLG  (also latches during clock load", text)
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


# Board 210 (Rev2) R0A Si5341A-D, 2026-10-16, firmware 5221fc7 (int_test/cm210_R0A.txt):
# locked to IN0 selected by register, only IN0 enabled and monitored, XAXB LOS
# disabled. The undefined bits set in 0x000C/0x000D/0x0011/0x0012/0x0021/0x002C
# are as read from the chip; the checks must ignore them.
BOARD210_R0A = {
    0x0002: 0x41, 0x0003: 0x53, 0x0004: 0x00, 0x0005: 0x03,
    0x000C: 0xC0, 0x000D: 0xF0, 0x0011: 0xD0, 0x0012: 0xF0,
    0x0021: 0x09, 0x002C: 0x31, 0x00E2: 0x03, 0x00FE: 0x0F,
    0x0102: 0x01, 0x0145: 0x00, 0x0206: 0x00, 0x090E: 0x00,
    0x091C: 0x04, 0x0949: 0x01, 0x094A: 0x10,
}
BOARD210_R0A.update({0x026B + i: b for i, b in enumerate(b"R0Av0006")})
# Variant: reference IN1 by register, IN0/IN1/FB_IN enabled and monitored.
SI5341_IN1 = _with(BOARD210_R0A, {0x0021: 0x0B, 0x002C: 0x3B, 0x0949: 0x0B})


def _run_5341(func, regs):
    clock = Si5341Clock(FakeUART(regs), address=0x10, name="R0A")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = func(clock, checks.read_monitors_5341(clock))
    return ok, out.getvalue()


class Si5341StatusTest(unittest.TestCase):
    def test_clean_status_passes_and_reads_no_si5395_registers(self):
        clock = Si5341Clock(FakeUART(BOARD210_R0A), address=0x10, name="R0A")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = checks.check_clock(clock, list(checks.CHECKS))
        text = out.getvalue()
        self.assertTrue(ok, text)
        self.assertIn("raw 0x000C-0x000D", text)
        self.assertIn("raw 0x000C-0x000D = C0 F0  locked", text)
        self.assertIn("reference IN0 (IN_SEL register), inputs enabled: IN0", text)
        self.assertIn("raw 0x0011-0x0012 = D0 F0", text)
        reads = [(int(f[3], 16) << 8) | int(f[4], 16)
                 for f in (w.decode().split() for w in clock.uart.writes)]
        for addr in (0x000E, 0x000F, 0x0013, 0x0014, 0x0507, 0x0535, 0x003F,
                     0x009A, 0x052A):
            self.assertNotIn(addr, reads, hex(addr))

    def test_lol_is_bit_3_and_always_a_problem(self):
        ok, text = _run_5341(checks.check_status_5341, _with(BOARD210_R0A, {0x000C: 0xC8}))
        self.assertFalse(ok)
        self.assertIn("UNLOCKED", text)
        self.assertIn("-> LOL", text)

    def test_bit_1_is_losxaxb_not_lol(self):
        # Board 210 has LOSXAXB_DIS=1 (crystal powered down, IN0 reference).
        ok, text = _run_5341(checks.check_status_5341, _with(BOARD210_R0A, {0x000C: 0xC2}))
        self.assertTrue(ok, text)
        self.assertIn("expected: LOSXAXB (XAXB LOS detection off (LOSXAXB_DIS=1))", text)
        self.assertNotIn("LOL", text)
        regs = _with(BOARD210_R0A, {0x000C: 0xC2, 0x002C: 0x21})
        ok, text = _run_5341(checks.check_status_5341, regs)
        self.assertFalse(ok)
        self.assertIn("-> LOSXAXB", text)

    def test_losref_is_a_problem(self):
        ok, text = _run_5341(checks.check_status_5341, _with(BOARD210_R0A, {0x000C: 0xC4}))
        self.assertFalse(ok)
        self.assertIn("-> LOSREF", text)

    def test_los_on_unselected_input_expected_on_reference_fails(self):
        ok, text = _run_5341(checks.check_status_5341, _with(SI5341_IN1, {0x000D: 0xF3}))
        self.assertFalse(ok)
        self.assertIn("-> LOS IN1", text)
        self.assertIn("expected: LOS IN0 (IN0 not the selected reference)", text)

    def test_los_on_disabled_or_unmonitored_input_expected(self):
        ok, text = _run_5341(checks.check_status_5341, _with(SI5341_IN1, {0x000D: 0xF4}))
        self.assertTrue(ok, text)
        self.assertIn("IN2 disabled", text)
        regs = _with(SI5341_IN1, {0x000D: 0xF2, 0x002C: 0x39})
        ok, text = _run_5341(checks.check_status_5341, regs)
        self.assertTrue(ok, text)
        self.assertIn("LOS detection off for IN1", text)

    def test_fb_in_los_expected_outside_zero_delay_mode(self):
        ok, text = _run_5341(checks.check_status_5341, _with(SI5341_IN1, {0x000D: 0xF8}))
        self.assertTrue(ok, text)
        self.assertIn("FB_IN unused outside zero delay mode", text)
        regs = _with(SI5341_IN1, {0x000D: 0xF8, 0x091C: 0x03})
        ok, text = _run_5341(checks.check_status_5341, regs)
        self.assertFalse(ok)
        self.assertIn("-> LOS FB_IN", text)

    def test_pin_selected_reference_flags_los_as_unknown(self):
        regs = _with(SI5341_IN1, {0x0021: 0x08, 0x000D: 0xF1})
        ok, text = _run_5341(checks.check_status_5341, regs)
        self.assertFalse(ok)
        self.assertIn("selected by IN_SEL pins", text)
        self.assertIn("-> LOS IN0 (reference selected by pins; cannot tell if used)", text)

    def test_flags_two_bytes_no_power_up_note(self):
        regs = _with(BOARD210_R0A, {0x0011: 0xD9, 0x0012: 0xF8})
        ok, text = _run_5341(checks.check_flags_5341, regs)
        self.assertFalse(ok)
        self.assertIn("raw 0x0011-0x0012 = D9 F8", text)
        self.assertIn("-> LOL_FLG\n", text)
        self.assertIn("expected: LOS_FLG FB_IN", text)

    def test_id_expects_5341(self):
        ok, text = _run_5341(checks.check_id, BOARD210_R0A)
        self.assertTrue(ok, text)
        self.assertIn("DEVICE_ID 0x5341  part Si5341A-D", text)
        self.assertIn("DESIGN_ID 'R0Av0006'  ACTIVE_NVM_BANK 0x03 (no user NVM burn)", text)


class WrongChipTest(unittest.TestCase):
    def test_check_clock_skips_on_pn_mismatch(self):
        # Si5395 PN where the board revision says Si5341.
        regs = _with(BOARD210_R0A, {0x0002: 0x95})
        clock = Si5341Clock(FakeUART(regs), address=0x10, name="R0A")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = checks.check_clock(clock, list(checks.CHECKS))
        self.assertFalse(ok)
        self.assertIn("WRONG CHIP", out.getvalue())
        self.assertNotIn("status:", out.getvalue())


class DumpLayoutTest(unittest.TestCase):
    def test_si5341_reads_no_si5395_only_registers(self):
        addrs = set(clock_dump.addresses(clock_dump.LAYOUTS["Si5341"]))
        for addr in (0x000E, 0x000F, 0x0013, 0x0014, 0x0103, 0x0135, 0x0247, 0x0265,
                     0x0507, 0x0515, 0x052A, 0x0535, 0x003F, 0x009A, 0x0A02, 0x0A14):
            self.assertNotIn(addr, addrs, hex(addr))
        for addr in (0x000C, 0x0021, 0x0108, 0x013A, 0x024A, 0x0268, 0x0235, 0x091C):
            self.assertIn(addr, addrs, hex(addr))

    def test_si5341_dump_renders(self):
        # Board 210 R0A status registers plus its dump's divider values: P0 = 3
        # (integer, no denominator), M = 125.11..., N0 = 21; OUT0 on N0 with R = 2.
        regs = dict(BOARD210_R0A)
        regs[0x0208] = 3
        regs.update(zip(range(0x0235, 0x023B), (360777252864).to_bytes(6, "little")))
        regs.update(zip(range(0x023B, 0x023F), (2883584000).to_bytes(4, "little")))
        regs.update(zip(range(0x0302, 0x0308), (45097156608).to_bytes(6, "little")))
        regs.update(zip(range(0x0308, 0x030C), (2147483648).to_bytes(4, "little")))
        regs.update({0x0108: 0x06, 0x0109: 0x09, 0x010B: 0x00, 0x0A03: 0x01})

        class Reg(object):
            def get_clock(self, name):
                return Si5341Clock(FakeUART(regs), address=0x10, name=name)

        args = argparse.Namespace(dev_path="/dev/fake", out_dir=None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            failed = clock_dump.dump_clock(Reg(), "R0A", args, [], None, echo=True)
        text = out.getvalue()
        self.assertFalse(failed, text)
        self.assertIn("Si5341 register dump", text)
        self.assertIn("part:      Si5341A-D", text)
        self.assertIn("0x000C  0xC0  SYSINCAL=0  LOSXAXB=0  LOSREF=0  LOL=0", text)
        self.assertIn("IN_SEL_REGCTRL=1(register)  IN_SEL=0(IN0)", text)
        self.assertIn("LOS_EN=1(IN0)  LOSXAXB_DIS=1", text)
        self.assertRegex(text, r"P0 +3 +- +3 ")
        self.assertIn("125.114181818", text)
        self.assertRegex(text, r"OUT0 +0  1 diff .* N0 .* 2\(F2\) +42\.000000")
        self.assertRegex(text, r"N0 .*21\.000000000 .* - +- ")
        self.assertNotIn("OUT0A", text)
        self.assertNotIn("MXAXB", text)


if __name__ == "__main__":
    unittest.main()
