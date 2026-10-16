#!/usr/bin/env python3
"""Read-only LGA80D checks from apollo_cm_mcu's lga80d-review-2026-10-09.md.

READ ONLY: every access is a ProgCom "r DC ..." read (PAGE select, then one
PMBus read). Nothing is written, cleared or reconfigured.

Checks (select with --check; the default runs all three):

  freq    Did LGA80D_init take effect? Reads FREQUENCY_SWITCH (33h, the
          programmed value), READ_FREQUENCY (95h, the measured value),
          VOUT_DROOP (28h) and MULTI_PHASE_RAMP_GAIN (D5h) and compares them
          with what LGA80D_init writes. The VCCINT modules have no SYNC strap
          and boot at 400 kHz, so 457 kHz there means init took effect.
          READ_FREQUENCY is only valid while the output is enabled: run with
          the board powered on.
  temp    READ_TEMPERATURE_1 (8Dh, controller die; what the firmware alarm
          and the IPMC use) against READ_TEMPERATURE_3 (8Fh, DrMOS, the
          module hotspot per the TRN), with READ_IOUT for context. Run under
          FPGA load.
  timing  The turn-on/off settings behind the firmware's sequencing:
          ON_OFF_CONFIG (02h), VOUT_COMMAND (21h), VOUT_UV_FAULT_LIMIT (44h),
          POWER_GOOD_ON (5Eh), TON_DELAY/TON_RISE/TOFF_DELAY/TOFF_FALL
          (60h/61h/64h/65h), DDC_CONFIG (D3h) and POWER_GOOD_DELAY (D4h).
          Derives the ZL8802 minimum EN pulse widths (Eq. 1/2) and the time
          from EN to PG, against the firmware's PG check 25 ms after EN.

Not covered: the scope measurement of EN against PG at turn-off.

Usage:
    lga80d_checks.py                              # all checks, all modules
    lga80d_checks.py --check freq                 # one check
    lga80d_checks.py --check temp --device F1VCCINT2
"""
import argparse
import os
import sys
import time

# Permit direct execution from this package directory (as exercise_mcu.py does).
if not __package__:
    _package_dir = os.path.dirname(os.path.realpath(__file__))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.core_config import CORE_CONFIG
from cm_interface.device.lga80d import LGA80D
from cm_interface.device.mcu import PowerFsmState
from cm_interface.errors import CMError
from cm_interface.registry import Registry
from cm_interface.utils import decode_linear11, decode_linear16u

EXPECTED_ERRORS = (CMError, OSError, TimeoutError, ValueError, RuntimeError)

# ZL8802 "PMBus Use Guidelines": 2 ms between reads and 5 ms before any other
# command to the same device. Every ProgCom read starts with a PAGE write, which
# would otherwise follow the previous read by only the UART round trip.
GAP_S = 0.01

# PMBus commands (ZL8802 FN8760 / LGA80D TRN 3.3)
ON_OFF_CONFIG = 0x02
VOUT_COMMAND = 0x21
VOUT_DROOP = 0x28
FREQUENCY_SWITCH = 0x33
VOUT_UV_FAULT_LIMIT = 0x44
POWER_GOOD_ON = 0x5E
TON_DELAY = 0x60
TON_RISE = 0x61
TOFF_DELAY = 0x64
TOFF_FALL = 0x65
STATUS_WORD = 0x79
READ_VIN = 0x88
READ_IOUT = 0x8C
READ_TEMPERATURE_1 = 0x8D
READ_TEMPERATURE_3 = 0x8F
READ_FREQUENCY = 0x95
DDC_CONFIG = 0xD3
POWER_GOOD_DELAY = 0xD4
MULTI_PHASE_RAMP_GAIN = 0xD5

STATUS_WORD_OFF = 1 << 6
PAGES = (0, 1)

# What LGA80D_init (cm_mcu LocalTasks.c) writes to every module except 3V3/1V8.
INIT_SKIPPED = ("3V3/1V8",)
INIT_FREQ_KHZ = 457.14   # FREQUENCY_SWITCH, 16 MHz / 35
INIT_DROOP = 0.07        # VOUT_DROOP in mV/A (sent as 573 * 2^-13 = 0.06995)
INIT_GAIN = 7            # MULTI_PHASE_RAMP_GAIN
INIT_TOL = 0.02

# Expected switching frequency, from the schematic SYNC straps plus LGA80D_init:
# 3V3/1V8 strap 38.3k = 800 kHz; AVTT/CC strap 23.7k = 457 kHz; VCCINT has no
# strap (open = 400 kHz) and reaches 457 kHz only through LGA80D_init.
STRAP_FREQ_KHZ = {"3V3/1V8": 800.0}
DEFAULT_FREQ_KHZ = 457.0
VCCINT_STRAP_FREQ_KHZ = 400.0
FREQ_TOL = 0.03          # READ_FREQUENCY has been seen at 457 and 463 kHz

VCCINT_PAIRS = (("F1VCCINT1", "F1VCCINT2"), ("F2VCCINT1", "F2VCCINT2"))

# Same plausibility window as cm_mcu MonitorTask.h (LGA80D_TEMP_MIN/MAX_C).
TEMP_MIN_C, TEMP_MAX_C = 0.0, 175.0

FW_PG_CHECK_MS = 25.0    # PowerSupplyTask checks PG one 25 ms period after EN
PRE_RAMP_MS = 2.0        # ~2 ms from enable to the start of the output ramp


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


def _read(lga, command, page, size):
    """One PMBus read of the register's real length, then the inter-command gap."""
    try:
        return lga.read_reg(LGA80D._paged_reg(command, page), size=size)
    finally:
        time.sleep(GAP_S)


def _u8(lga, command, page=0):
    return _read(lga, command, page, 1)[0]


def _u16(lga, command, page=0):
    return int.from_bytes(_read(lga, command, page, 2), "little")


def _l11(lga, command, page=0):
    return decode_linear11(_read(lga, command, page, 2))


def _l16u(lga, command, page=0):
    return decode_linear16u(_read(lga, command, page, 2))


def _near(value, target, tol):
    return abs(value - target) <= tol * abs(target)


def _onoff(enabled):
    return "/".join("on" if e else "off" for e in enabled)


def read_power_state(reg):
    try:
        state = reg.get_mcu().read_power().fsm_state
        print("MCU power state: %s" % state.name)
        return state
    except EXPECTED_ERRORS as exc:
        print("MCU power state: unknown (%s)" % _format_error(exc))
        return None


def check_freq(reg, names, power_state):
    """FREQUENCY_SWITCH / READ_FREQUENCY / VOUT_DROOP / ramp gain vs LGA80D_init."""
    print("\n=== freq: did LGA80D_init take effect? ===")
    if power_state is not None and power_state != PowerFsmState.POWER_ON:
        print("WARNING: power is not ON; READ_FREQUENCY is only valid on enabled outputs")
    ok = True
    results = {}
    for name in names:
        lga = reg.get_lga80d(name)
        try:
            enabled = [not (_u16(lga, STATUS_WORD, p) & STATUS_WORD_OFF) for p in PAGES]
            prog = _l11(lga, FREQUENCY_SWITCH)             # global
            meas = _l11(lga, READ_FREQUENCY)               # global
            droop = [_l11(lga, VOUT_DROOP, p) for p in PAGES]
            gain = _u8(lga, MULTI_PHASE_RAMP_GAIN)         # global
        except EXPECTED_ERRORS as exc:
            ok = False
            print("%-10s READ FAILED: %s" % (name, _format_error(exc)))
            continue
        results[name] = (prog, droop, gain)
        print("%-10s outputs p0/p1 %-7s FREQUENCY_SWITCH %6.1f kHz  READ_FREQUENCY %6.1f kHz  "
              "VOUT_DROOP %.4f/%.4f mV/A  RAMP_GAIN %d"
              % (name, _onoff(enabled), prog, meas, droop[0], droop[1], gain))

        notes = []
        if name not in INIT_SKIPPED:
            if not _near(prog, INIT_FREQ_KHZ, INIT_TOL):
                notes.append("FREQUENCY_SWITCH is not the %.2f kHz init writes" % INIT_FREQ_KHZ)
            for p in PAGES:
                if not _near(droop[p], INIT_DROOP, INIT_TOL):
                    notes.append("page %d VOUT_DROOP is not the %.2f mV/A init writes"
                                 % (p, INIT_DROOP))
            if gain != INIT_GAIN:
                notes.append("MULTI_PHASE_RAMP_GAIN is not the %d init writes" % INIT_GAIN)
        expected = STRAP_FREQ_KHZ.get(name, DEFAULT_FREQ_KHZ)
        if not any(enabled):
            notes.append("both outputs off: READ_FREQUENCY not meaningful")
        elif _near(meas, expected, FREQ_TOL):
            if "VCCINT" in name:
                notes.append("switching at ~457 kHz: init took effect")
        elif "VCCINT" in name and _near(meas, VCCINT_STRAP_FREQ_KHZ, FREQ_TOL):
            notes.append("switching at the 400 kHz no-strap default: init did NOT take effect")
        else:
            notes.append("READ_FREQUENCY differs from the expected %.0f kHz" % expected)
        for note in notes:
            print("           -> %s" % note)

    for a, b in VCCINT_PAIRS:
        if a in results and b in results:
            same = (_near(results[a][0], results[b][0], INIT_TOL)
                    and all(_near(results[a][1][p], results[b][1][p], INIT_TOL) for p in PAGES)
                    and results[a][2] == results[b][2])
            print("4-phase pair %s/%s: %s" % (a, b, "consistent" if same else
                                               "MISMATCH in FREQUENCY_SWITCH, VOUT_DROOP or gain"))
    return ok


def check_temp(reg, names, power_state):
    """READ_TEMPERATURE_1 (controller die) against READ_TEMPERATURE_3 (DrMOS)."""
    print("\n=== temp: controller die (8Dh) vs DrMOS hotspot (8Fh) ===")
    if power_state is not None and power_state != PowerFsmState.POWER_ON:
        print("WARNING: power is not ON; the comparison is meant to be made under load")
    print("%-10s page  TEMP_1 (C)  TEMP_3 (C)  T3-T1 (C)  IOUT (A)" % "module")
    ok = True
    max_t1 = None
    max_t3 = None
    for name in names:
        lga = reg.get_lga80d(name)
        for page in PAGES:
            try:
                raw1 = _read(lga, READ_TEMPERATURE_1, page, 2)
                raw3 = _read(lga, READ_TEMPERATURE_3, page, 2)
                iout = _l11(lga, READ_IOUT, page)
            except EXPECTED_ERRORS as exc:
                ok = False
                print("%-10s  %d    READ FAILED: %s" % (name, page, _format_error(exc)))
                continue
            t1 = decode_linear11(raw1)
            t3 = decode_linear11(raw3)
            valid1 = TEMP_MIN_C <= t1 <= TEMP_MAX_C
            valid3 = TEMP_MIN_C <= t3 <= TEMP_MAX_C
            line = "%-10s  %d    %8.2f    %8.2f   %8.2f   %7.2f" % (name, page, t1, t3, t3 - t1, iout)
            if not valid1:
                line += "  TEMP_1 implausible (raw 0x%04X)" % int.from_bytes(raw1, "little")
            if not valid3:
                line += "  TEMP_3 implausible (raw 0x%04X)" % int.from_bytes(raw3, "little")
            print(line)
            if valid1 and (max_t1 is None or t1 > max_t1[0]):
                max_t1 = (t1, name, page)
            if valid3 and (max_t3 is None or t3 > max_t3[0]):
                max_t3 = (t3, name, page)
    if max_t1 is not None and max_t3 is not None:
        print("hottest TEMP_1 (what the alarm and IPMC see): %.2f C (%s page %d)" % max_t1)
        print("hottest TEMP_3 (DrMOS):                       %.2f C (%s page %d)" % max_t3)
        print("difference: %.2f C" % (max_t3[0] - max_t1[0]))
    return ok


def _on_off_config_text(value):
    source = (value >> 2) & 0x7
    sources = {0b101: "EN pin only", 0b110: "OPERATION only"}
    text = sources.get(source, "start source 0b{:03b}".format(source))
    off = "immediate off" if value & 0x1 else "soft off (TOFF_FALL)"
    return "%s, %s" % (text, off)


def check_timing(reg, names):
    """Turn-on/off settings, ZL8802 Eq. 1/2 and time to PG vs the 25 ms check."""
    print("\n=== timing: turn-on/off settings vs firmware sequencing ===")
    ok = True
    for name in names:
        lga = reg.get_lga80d(name)
        try:
            vin = _l11(lga, READ_VIN)                      # global
            freq_khz = _l11(lga, FREQUENCY_SWITCH)         # global
            gain = _u8(lga, MULTI_PHASE_RAMP_GAIN)         # global
        except EXPECTED_ERRORS as exc:
            ok = False
            print("%-10s READ FAILED: %s" % (name, _format_error(exc)))
            continue
        for page in PAGES:
            try:
                on_off = _u8(lga, ON_OFF_CONFIG, page)
                ddc = _u16(lga, DDC_CONFIG, page)
                vout = _l16u(lga, VOUT_COMMAND, page)
                pg_on = _l16u(lga, POWER_GOOD_ON, page)
                uv = _l16u(lga, VOUT_UV_FAULT_LIMIT, page)
                ton_d = _l11(lga, TON_DELAY, page)
                ton_r = _l11(lga, TON_RISE, page)
                toff_d = _l11(lga, TOFF_DELAY, page)
                toff_f = _l11(lga, TOFF_FALL, page)
                pg_delay = _l11(lga, POWER_GOOD_DELAY, page)
            except EXPECTED_ERRORS as exc:
                ok = False
                print("%-10s p%d READ FAILED: %s" % (name, page, _format_error(exc)))
                continue

            phases = (ddc & 0x7) + 1
            multiphase = phases >= 4
            print("%-10s p%d: DDC_CONFIG 0x%04X (rail %d, phases in rail %d, phase id %d)  "
                  "ON_OFF_CONFIG 0x%02X (%s)"
                  % (name, page, ddc, (ddc >> 8) & 0x1F, phases, (ddc >> 13) & 0x7,
                     on_off, _on_off_config_text(on_off)))
            print("     TON_DELAY %.2f  TON_RISE %.2f  TOFF_DELAY %.2f  TOFF_FALL %.2f  "
                  "POWER_GOOD_DELAY %.2f ms" % (ton_d, ton_r, toff_d, toff_f, pg_delay))
            if vout > 0:
                print("     VOUT_COMMAND %.4f V  POWER_GOOD_ON %.4f V (%.0f%%)  "
                      "VOUT_UV_FAULT_LIMIT %.4f V (%.0f%%; PG drops below this)"
                      % (vout, pg_on, 100.0 * pg_on / vout, uv, 100.0 * uv / vout))
            else:
                print("     VOUT_COMMAND %.4f V  POWER_GOOD_ON %.4f V  VOUT_UV_FAULT_LIMIT %.4f V"
                      % (vout, pg_on, uv))

            en_low_min = toff_d + toff_f + 10.5            # ZL8802 Eq. 1
            en_high_min = ton_d + ton_r + pg_delay + 5.5   # ZL8802 Eq. 2
            note = ""
            if multiphase:
                note = "  (4-phase: TOFF_FALL is ignored, Eq. 1 as written)"
            print("     EN low min %.1f ms (Eq. 1), EN high min %.1f ms (Eq. 2)%s"
                  % (en_low_min, en_high_min, note))

            # 4-phase rails ramp at the MULTI_PHASE_RAMP_GAIN slew rate instead of
            # TON_RISE: slew (mV/ms) = 14 * VIN * gain * f(MHz) (ZL8802 Eq. 8).
            if multiphase and vin > 0 and gain > 0 and freq_khz > 0:
                rise = 1000.0 * vout / (14.0 * vin * gain * freq_khz / 1000.0)
                rise_text = "rise %.2f ms from ramp gain %d, VIN %.2f V, %.1f kHz" % (
                    rise, gain, vin, freq_khz)
            else:
                rise = ton_r
                rise_text = "TON_RISE %.2f ms" % rise
            t_pg = PRE_RAMP_MS + ton_d + rise + pg_delay
            print("     PG ~%.1f ms after EN (%.0f ms pre-ramp + TON_DELAY + %s + PG delay); "
                  "margin %.1f ms to the firmware's %.0f ms PG check"
                  % (t_pg, PRE_RAMP_MS, rise_text, FW_PG_CHECK_MS - t_pg, FW_PG_CHECK_MS))
    return ok


CHECKS = ("freq", "temp", "timing")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="append", choices=CHECKS,
                        help="check to run (repeatable; default: all)")
    parser.add_argument("--device", action="append", choices=list(CORE_CONFIG.lga80d),
                        help="LGA80D supply name (repeatable; default: all)")
    parser.add_argument("--dev-path", default="/dev/ttyUL4",
                        help="UART device (default: %(default)s)")
    parser.add_argument("--debug", action="store_true",
                        help="log every UART transaction to stdout")
    args = parser.parse_args()

    checks = args.check or list(CHECKS)
    names = args.device or list(CORE_CONFIG.lga80d)
    reg = Registry(dev_path=args.dev_path, debug=sys.stdout if args.debug else None)

    power_state = read_power_state(reg)
    ok = True
    if "freq" in checks:
        ok = check_freq(reg, names, power_state) and ok
    if "temp" in checks:
        ok = check_temp(reg, names, power_state) and ok
    if "timing" in checks:
        ok = check_timing(reg, names) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
