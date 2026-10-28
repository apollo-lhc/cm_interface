#!/usr/bin/env python3
"""Read-only clock register checks (Si5395, and the Si5341 at R0A on Rev2).

READ ONLY: every access is a ProgCom "r CL ..." read. Nothing is written;
sticky flags are reported but not cleared.

The chip at each position comes from the board revision the MCU reports
(Registry.clocks). DEVICE_READY (0x00FE) is read first. Until it reads 0x0F
the reference manuals permit no other register access, so a clock that is
not ready is reported and skipped. Then PN_BASE must match the chip the board
revision implies; on a mismatch the clock is reported and skipped, since its
status bits would be decoded with the wrong register map.

Si5395 checks (select with --check; the default runs all three):

  id      DEVICE_ID (0x0002, expect 0x5395), GRADE, DEVICE_REV, TEMP_GRADE,
          PKG_ID, DESIGN_ID (0x026B, CBPro string) and ACTIVE_NVM_BANK
          (0x00E2: how many NVM banks the user has burned).
  status  Live status 0x000C-0x000F (internal errors, LOS/OOF per input,
          LOL, holdover, calibration), the active input IN_ACTV (0x0507),
          input selection mode (0x052A) and the global output enables
          OUTALL_DISABLE_LOW (0x0102) and OUT_PDN_ALL (0x0145).
  flags   Sticky flags 0x0011-0x0014: anything that happened since the
          flags were last cleared.

Expected alarms. Before judging status or flags, the configuration that
decides what an alarm means is read: IN_EN (0x0949), LOS_EN (0x002C),
OOF_EN/FAST_OOF_EN (0x003F), LOL_SLOW_EN_PLL (0x009A), FORCE_HOLD (0x0535)
and LOSXAXB_DIS (0x002C). An alarm is shown but does not fail the check when
the configuration explains it:
  LOS/OOF   input disabled, its detector disabled, or not the active input
            (or the device is in free run, so no input is used)
  LOL       free run (FORCE_HOLD=1 or no input enabled): nothing to lock to;
            or LOL detection disabled (LOL_SLOW_EN_PLL=0)
  HOLD      FORCE_HOLD=1, or no input enabled (free run)
  LOSXAXB   LOSXAXB_DIS=1
Sticky flags and clock loading: the MCU clears the sticky flags after it
loads the clocks (load_all_clocks -> clear_clk_stickybits), so a set flag is
an event after configuration. Firmware whose clear_clk_stickybits() does not
select page 0 first (cm_mcu master up to dcfe0d2; fixed on the fix/clksticky
and fix/powering branches, 2026-10-16) misses the flags. On such firmware SYSINCAL_FLG, LOSXAXB_FLG, XAXB_ERR_FLG and
CAL_FLG_PLL, which also latch while the device calibrates after a load or
reset, may be load artifacts; they are noted as such but still fail.

Si5341 (Si5341/40 Rev D RM Rev 1.3, chapter 14) differs:

  id      as above, expect 0x5341.
  status  0x000C-0x000D only: SYSINCAL, LOSXAXB, LOSREF, LOL (0x000C bit 3),
          SMBUS_TIMEOUT, LOS per input IN0, IN1, IN2, FB_IN. There is no
          holdover, OOF or PLL calibration status. Reference selection is
          0x0021 (IN_SEL 3 = XA/XB); outputs as above.
  flags   Sticky flags 0x0011-0x0012.

  Expected alarms: LOS on an input that is disabled (IN_EN 0x0949), has LOS
  detection off (LOS_EN 0x002C) or is not the register-selected reference;
  LOS on FB_IN unless zero-delay mode is on (ZDM_EN 0x091C = 3); LOSXAXB with
  LOSXAXB_DIS=1. LOL and LOSREF are always alarms: the PLL locks to whichever
  reference is selected, the crystal included. With pin-controlled selection
  (IN_SEL_REGCTRL=0) the selected input cannot be read, so LOS on an enabled,
  monitored input is reported as a problem. These rules come from the
  reference manual and have not been checked on a Rev2 board.

Usage:
    cm-clock-checks                         # all checks, all clocks
    cm-clock-checks --check status          # one check
    cm-clock-checks R0A R1B --check flags   # some clocks
    cm-clock-checks --hw-rev 2              # override the MCU's board revision
"""
import argparse
import os
import sys
from collections import namedtuple

# Permit direct execution from a checkout (./tools/NAME.py) without the RPM.
if not __package__:
    _package_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.core_config import CORE_CONFIG
from cm_interface.device.clock import DEVICE_READY_VALUE
from cm_interface.device.si5341 import Si5341Reg
from cm_interface.device.si5395 import Si5395Reg
from cm_interface.errors import ClockIdentityError, CMError
from cm_interface.tools._common import (add_common_args, add_device_args, add_force_arg,
                                       make_registry, power_info, power_is_on, print_summary,
                                       selected_devices)

EXPECTED_ERRORS = (CMError, OSError, TimeoutError, ValueError, RuntimeError)

IN_ACTV = 0x0507          # bits 7:6, currently selected DSPLL input (read only)
LOS_EN = 0x002C           # bits 3:0 LOS_EN per input, bit 4 LOSXAXB_DIS
OOF_EN = 0x003F           # bits 3:0 OOF_EN, bits 7:4 FAST_OOF_EN per input
LOL_SLOW_EN = 0x009A      # bit 1 LOL_SLOW_EN_PLL
FORCE_HOLD = 0x0535       # bit 0
IN_EN = 0x0949            # bits 3:0 input buffer enable per input

# ACTIVE_NVM_BANK (0x00E2): Si5395 RM table 16.62; same values in the Si5341 RM.
NVM_BANKS = {0x03: "no user NVM burn", 0x0F: "1 bank burned", 0x3F: "2 banks burned (last bank used)"}


def _format_error(exc):
    """Return a compact message that includes useful chained exceptions."""
    messages = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip() or type(current).__name__
        if message not in messages:
            messages.append(message)
        current = current.__cause__ or current.__context__
    return " (caused by: ".join(messages) + ")" * (len(messages) - 1)


def _u8(clock, reg):
    return clock.read_reg(reg)[0]


def _bit(value, n):
    return bool(value & (1 << n))


def _inputs(mask):
    """Names of the inputs IN0-IN3 whose bit is set in a 4-bit mask."""
    names = ["IN%d" % i for i in range(4) if mask & (1 << i)]
    return ",".join(names) if names else "none"


def _letter(value):
    """GRADE / DEVICE_REV encoding: 0 = A, 1 = B, ..."""
    return chr(ord("A") + value) if 0 <= value < 26 else "?"


class Monitors(namedtuple("Monitors", "in_en los_en oof_en losxaxb_dis lol_en "
                                       "force_hold in_actv")):
    """Configuration that decides whether a status bit is an alarm."""

    @property
    def free_run(self):
        return self.force_hold or not self.in_en

    def free_run_reason(self):
        return "FORCE_HOLD=1" if self.force_hold else "no input enabled (IN_EN=0)"

    def describe(self):
        if self.free_run:
            return "free run / forced holdover from XAXB (%s)" % self.free_run_reason()
        return "locking to IN%d (inputs enabled: %s)" % (self.in_actv, _inputs(self.in_en))

    def input_reason(self, kind, n):
        """Why LOS/OOF on input n is expected, or None when it is a real alarm."""
        if not self.in_en & (1 << n):
            return "IN%d disabled" % n
        detector = self.los_en if kind == "LOS" else self.oof_en
        if not detector & (1 << n):
            return "%s detection off for IN%d" % (kind, n)
        if self.free_run:
            return "free run, no input used"
        if n != self.in_actv:
            return "IN%d not the active input" % n
        return None

    def lol_reason(self):
        if self.free_run:
            return "free run with %s, nothing to lock to" % self.free_run_reason()
        if not self.lol_en:
            return "LOL detection off (LOL_SLOW_EN_PLL=0)"
        return None

    def hold_reason(self):
        return "free run with %s" % self.free_run_reason() if self.free_run else None

    def losxaxb_reason(self):
        return "XAXB LOS detection off (LOSXAXB_DIS=1)" if self.losxaxb_dis else None


def read_monitors(clock):
    los_en = _u8(clock, LOS_EN)
    oof_en = _u8(clock, OOF_EN)
    return Monitors(
        in_en=_u8(clock, IN_EN) & 0xF,
        los_en=los_en & 0xF,
        oof_en=(oof_en | (oof_en >> 4)) & 0xF,
        losxaxb_dis=_bit(los_en, 4),
        lol_en=_bit(_u8(clock, LOL_SLOW_EN), 1),
        force_hold=_bit(_u8(clock, FORCE_HOLD), 0),
        in_actv=(_u8(clock, IN_ACTV) >> 6) & 0x3,
    )


def classify(mon, sysincal, los_oof, lol_hold, cal_pll, sticky=False):
    """Split the set bits of the four status (or sticky flag) bytes into
    (problems, expected); expected entries carry the reason."""
    sfx = "_FLG" if sticky else ""
    problems, expected = [], []

    def add(name, reason):
        (expected if reason else problems).append(
            "%s (%s)" % (name, reason) if reason else name)

    if _bit(sysincal, 0):
        add("SYSINCAL" + sfx, None)
    if _bit(sysincal, 1):
        add("LOSXAXB" + sfx, mon.losxaxb_reason())
    if _bit(sysincal, 3):
        add("XAXB_ERR" + sfx, None)
    if _bit(sysincal, 5):
        add("SMBUS_TIMEOUT" + sfx, None)
    for n in range(4):
        if _bit(los_oof, n):
            add("LOS%s IN%d" % (sfx, n), mon.input_reason("LOS", n))
        if _bit(los_oof, n + 4):
            add("OOF%s IN%d" % (sfx, n), mon.input_reason("OOF", n))
    if _bit(lol_hold, 1):
        add("LOL" + sfx, mon.lol_reason())
    if _bit(lol_hold, 5):
        add("HOLD" + sfx, mon.hold_reason())
    if _bit(cal_pll, 5):
        add("CAL_PLL" if not sticky else "CAL_FLG_PLL", None)
    return problems, expected


def check_ready(clock):
    """Read only DEVICE_READY; return True when the clock may be accessed further."""
    ready = _u8(clock, clock.REG.DEVICE_READY)
    if ready != DEVICE_READY_VALUE:
        print("%-4s NOT READY: DEVICE_READY 0x%02X (expect 0x%02X); skipping further reads"
              % (clock.name, ready, DEVICE_READY_VALUE))
        return False
    return True


def check_identity(clock):
    """PN_BASE must match the chip the board revision implies."""
    try:
        clock.verify_identity()
    except ClockIdentityError as exc:
        print("%-4s WRONG CHIP: %s; skipping further reads" % (clock.name, exc))
        return False
    return True


def check_id(clock, mon=None):
    """Identity and NVM state. Returns True when nothing is unexpected."""
    device_id = clock.get_device_id()
    grade = clock.grade
    rev = clock.device_revision
    temp_grade = clock.temperature_grade
    pkg = clock.package_id
    design_id = clock.design_id
    nvm = _u8(clock, clock.REG.ACTIVE_NVM_BANK)
    print("%-4s id: DEVICE_ID 0x%04X  part Si%04X%s-%s  TEMP_GRADE %d  PKG_ID %d  "
          "DESIGN_ID %r  ACTIVE_NVM_BANK 0x%02X (%s)"
          % (clock.name, device_id, device_id, _letter(grade), _letter(rev), temp_grade,
             pkg, design_id, nvm, NVM_BANKS.get(nvm, "unexpected value")))
    # PN_BASE was already checked by check_identity.
    if nvm not in NVM_BANKS:
        print("     -> ACTIVE_NVM_BANK is not one of the documented values")
        return False
    return True


def check_status(clock, mon):
    """Live (non-sticky) status. Returns True when the clock is healthy."""
    sysincal, los_oof, lol_hold, cal_pll = clock.read_reg(Si5395Reg.SYSINCAL, size=4)
    in_sel = _u8(clock, Si5395Reg.IN_SEL_REGCTRL)
    outall = _u8(clock, Si5395Reg.OUTALL_DISABLE)
    pdn_all = _u8(clock, Si5395Reg.OUT_PDN_ALL)

    lol = _bit(lol_hold, 1)
    hold = _bit(lol_hold, 5)
    print("%-4s status: raw 0x000C-0x000F = %02X %02X %02X %02X  %s%s"
          % (clock.name, sysincal, los_oof, lol_hold, cal_pll,
             "UNLOCKED" if lol else "locked", ", HOLDOVER/free run" if hold else ""))
    print("     mode: %s" % mon.describe())
    print("     LOS %s  OOF %s  active input IN%d  selection %s (IN_SEL IN%d)"
          % (_inputs(los_oof & 0xF), _inputs(los_oof >> 4), mon.in_actv,
             "register" if _bit(in_sel, 0) else "pin", (in_sel >> 1) & 0x3))
    print("     outputs: OUTALL_DISABLE_LOW %d (%s)  OUT_PDN_ALL %d (%s)"
          % (outall & 1, "enabled" if _bit(outall, 0) else "ALL DISABLED",
             pdn_all & 1, "ALL POWERED DOWN" if _bit(pdn_all, 0) else "no effect"))

    problems, expected = classify(mon, sysincal, los_oof, lol_hold, cal_pll)
    if not _bit(outall, 0):
        problems.append("all outputs disabled (OUTALL_DISABLE_LOW = 0)")
    if _bit(pdn_all, 0):
        problems.append("all output drivers powered down (OUT_PDN_ALL = 1)")
    for item in expected:
        print("     expected: %s" % item)
    for problem in problems:
        print("     -> %s" % problem)
    return not problems


# Si5395 sticky flags that also latch during calibration after a load or reset.
POWER_UP_FLAGS = ("SYSINCAL_FLG", "LOSXAXB_FLG", "XAXB_ERR_FLG", "CAL_FLG_PLL")


def check_flags(clock, mon):
    """Sticky flags 0x0011-0x0014. Returns True when no unexplained flag is set."""
    sys_flg, los_oof_flg, lol_hold_flg, cal_flg = clock.sticky_flags
    print("%-4s flags: raw 0x0011-0x0014 = %02X %02X %02X %02X"
          % (clock.name, sys_flg, los_oof_flg, lol_hold_flg, cal_flg))
    problems, expected = classify(mon, sys_flg, los_oof_flg, lol_hold_flg, cal_flg,
                                  sticky=True)
    for item in expected:
        print("     expected: %s" % item)
    for flag in problems:
        note = ("  (also latches during clock load; see --help)"
                if flag.split()[0] in POWER_UP_FLAGS else "")
        print("     -> %s%s" % (flag, note))
    if problems or expected:
        print("     (sticky since last clear; this script does not clear them)")
    return not problems


# ---------------------------------------------------------------- Si5341

SI5341_INPUTS = ("IN0", "IN1", "IN2", "FB_IN")
SI5341_REFS = ("IN0", "IN1", "IN2", "XA/XB")
ZDM_ON = 3                # ZDM_EN (0x091C bits 2:0): 3 = zero delay, 4 = normal


def _inputs_5341(mask):
    names = [SI5341_INPUTS[i] for i in range(4) if mask & (1 << i)]
    return ",".join(names) if names else "none"


class Monitors5341(namedtuple("Monitors5341", "in_en los_en losxaxb_dis regctrl "
                                               "in_sel zdm")):
    """Si5341 configuration that decides whether a status bit is an alarm."""

    @property
    def reference(self):
        """Selected reference name, or None when selected by pins."""
        return SI5341_REFS[self.in_sel] if self.regctrl else None

    def describe(self):
        if self.reference is None:
            ref = "reference selected by IN_SEL pins (not readable)"
        else:
            ref = "reference %s (IN_SEL register)" % self.reference
        return "%s, inputs enabled: %s%s" % (ref, _inputs_5341(self.in_en),
                                             ", zero delay mode" if self.zdm else "")

    def input_reason(self, n):
        """Why LOS on input n (3 = FB_IN) is expected, or None for a real alarm."""
        name = SI5341_INPUTS[n]
        if not self.in_en & (1 << n):
            return "%s disabled" % name
        if not self.los_en & (1 << n):
            return "LOS detection off for %s" % name
        if n == 3:
            return None if self.zdm else "FB_IN unused outside zero delay mode"
        if self.reference is not None and self.reference != name:
            return "%s not the selected reference" % name
        return None

    def losxaxb_reason(self):
        return "XAXB LOS detection off (LOSXAXB_DIS=1)" if self.losxaxb_dis else None


def read_monitors_5341(clock):
    los_en = _u8(clock, Si5341Reg.LOS_ENABLE)
    in_sel = _u8(clock, Si5341Reg.IN_SEL_REGCTRL)
    return Monitors5341(
        in_en=_u8(clock, Si5341Reg.IN_EN) & 0xF,
        los_en=los_en & 0xF,
        losxaxb_dis=_bit(los_en, 4),
        regctrl=_bit(in_sel, 0),
        in_sel=(in_sel >> 1) & 0x3,
        zdm=(_u8(clock, Si5341Reg.ZDM_EN) & 0x7) == ZDM_ON,
    )


def classify_5341(mon, status, los, sticky=False):
    """Split the set bits of 0x000C/0x000D (or the sticky 0x0011/0x0012) into
    (problems, expected); expected entries carry the reason."""
    sfx = "_FLG" if sticky else ""
    problems, expected = [], []

    def add(name, reason):
        (expected if reason else problems).append(
            "%s (%s)" % (name, reason) if reason else name)

    if _bit(status, 0):
        add("SYSINCAL" + sfx, None)
    if _bit(status, 1):
        add("LOSXAXB" + sfx, mon.losxaxb_reason())
    if _bit(status, 2):
        add("LOSREF" + sfx, None)
    if _bit(status, 3):
        add("LOL" + sfx, None)
    if _bit(status, 5):
        add("SMBUS_TIMEOUT" + sfx, None)
    for n in range(4):
        if _bit(los, n):
            add("LOS%s %s" % (sfx, SI5341_INPUTS[n]), mon.input_reason(n))
    return problems, expected


def check_status_5341(clock, mon):
    """Si5341 live status. Returns True when the clock is healthy."""
    status, los = clock.read_reg(Si5341Reg.STATUS, size=2)
    outall = _u8(clock, Si5341Reg.OUTALL_DISABLE)
    pdn_all = _u8(clock, Si5341Reg.OUT_PDN_ALL)

    print("%-4s status: raw 0x000C-0x000D = %02X %02X  %s"
          % (clock.name, status, los, "UNLOCKED" if _bit(status, 3) else "locked"))
    print("     mode: %s" % mon.describe())
    print("     LOS %s" % _inputs_5341(los & 0xF))
    print("     outputs: OUTALL_DISABLE_LOW %d (%s)  OUT_PDN_ALL %d (%s)"
          % (outall & 1, "enabled" if _bit(outall, 0) else "ALL DISABLED",
             pdn_all & 1, "ALL POWERED DOWN" if _bit(pdn_all, 0) else "no effect"))

    problems, expected = classify_5341(mon, status, los)
    if mon.reference is None:
        problems = [p + " (reference selected by pins; cannot tell if used)"
                    if p.startswith("LOS IN") else p for p in problems]
    if not _bit(outall, 0):
        problems.append("all outputs disabled (OUTALL_DISABLE_LOW = 0)")
    if _bit(pdn_all, 0):
        problems.append("all output drivers powered down (OUT_PDN_ALL = 1)")
    for item in expected:
        print("     expected: %s" % item)
    for problem in problems:
        print("     -> %s" % problem)
    return not problems


def check_flags_5341(clock, mon):
    """Si5341 sticky flags 0x0011-0x0012. Returns True when no unexplained flag is set."""
    status_flg, los_flg = clock.sticky_flags
    print("%-4s flags: raw 0x0011-0x0012 = %02X %02X" % (clock.name, status_flg, los_flg))
    problems, expected = classify_5341(mon, status_flg, los_flg, sticky=True)
    for item in expected:
        print("     expected: %s" % item)
    for flag in problems:
        print("     -> %s" % flag)
    if problems or expected:
        print("     (sticky since last clear; this script does not clear them)")
    return not problems


# ---------------------------------------------------------------- driver

def check_clock(clock, checks):
    """Run the selected checks on one clock. Returns check -> pass/fail for
    each check that ran; empty if the clock is not ready, is the wrong chip
    or its configuration could not be read."""
    results = {}
    name = clock.name
    try:
        if not check_ready(clock) or not check_identity(clock):
            return results
    except EXPECTED_ERRORS as exc:
        print("%-4s READ FAILED: %s" % (name, _format_error(exc)))
        return results
    profile = PROFILES[clock.CHIP]
    mon = None
    if set(checks) & {"status", "flags"}:
        try:
            mon = profile["monitors"](clock)
        except EXPECTED_ERRORS as exc:
            print("%-4s config READ FAILED: %s" % (name, _format_error(exc)))
            return results
    for check in checks:
        try:
            results[check] = bool(profile[check](clock, mon))
        except EXPECTED_ERRORS as exc:
            print("%-4s %s READ FAILED: %s" % (name, check, _format_error(exc)))
            results[check] = False
    return results


CHECKS = ("id", "status", "flags")
PROFILES = {
    "Si5395": {"monitors": read_monitors, "id": check_id,
               "status": check_status, "flags": check_flags},
    "Si5341": {"monitors": read_monitors_5341, "id": check_id,
               "status": check_status_5341, "flags": check_flags_5341},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(parser, "clock", list(CORE_CONFIG.clocks), default_all=True)
    parser.add_argument("--check", action="append", choices=CHECKS,
                        help="check to run (repeatable; default: all)")
    parser.add_argument("--hw-rev", type=int, metavar="N",
                        help="board revision to assume instead of the MCU's "
                             "HARDWARE_REVISION (e.g. EEPROM not programmed)")
    add_force_arg(parser)
    add_common_args(parser)
    args = parser.parse_args()

    checks = [c for c in CHECKS if c in (args.check or CHECKS)]
    names = selected_devices(parser, args, list(CORE_CONFIG.clocks), default_all=True)
    reg = make_registry(args, hw_rev=args.hw_rev)
    power_text, power_state = power_info(reg)
    print("MCU power state: %s" % power_text)
    if not power_is_on(power_state, args.force):
        return 1
    try:
        clocks = reg.clocks
    except EXPECTED_ERRORS as exc:
        print("cannot determine clock chips: %s (use --hw-rev N to set the board "
              "revision)" % _format_error(exc))
        return 1
    print("board revision %d%s: %s" % (
        reg.hw_rev, " (--hw-rev)" if args.hw_rev is not None else " (MCU)",
        "  ".join("%s=%s" % (n, clocks[n].CHIP) for n in names)))

    results = {}
    for name in names:
        print()
        results[name] = check_clock(reg.get_clock(name), checks)
    return 0 if print_summary(names, checks, results) else 1


if __name__ == "__main__":
    sys.exit(main())
