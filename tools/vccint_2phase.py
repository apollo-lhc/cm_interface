#!/usr/bin/env python3
"""Reconfigure FnVCCINT (n = 1 or 2, --fpga, required) from 4-phase
(FnVCCINT1 + FnVCCINT2) to 2-phase (FnVCCINT1 only), overriding the CFG strap
resistors over PMBus.

NOT PERSISTENT: nothing is stored (no STORE_USER_ALL). After any LGA80D VIN
cycle the modules come back with their straps (4-phase master/slave) and
this script must be run again, before the supplies are enabled.

Steps (LGA80D_40A_dual_AppNote.pdf, ZL8802 datasheet):
  1. FnVCCINT2, both pages: ON_OFF_CONFIG (02h) = 0x1B (start from OPERATION
     only, ignore the shared EN pin), then OPERATION (01h) = 0x00 (off).
  2. FnVCCINT1, both pages: DDC_CONFIG (D3h) -> same rail ID, phase IDs 0 and
     1, phases-in-rail reduced by 2. The datasheet describes the field as
     "number of phases (+1)", which is ambiguous, so the new value is derived
     from the value read back (current - 2), and the current value must be
     the 4-phase one (3 or 4) or the script refuses.
  3. Optional, --drop-rail N: FnVCCINT2, both pages: DDC_CONFIG -> rail ID N,
     phase IDs 0 and 1, same phases field as FnVCCINT1's new value, so it is
     a self-consistent 2-phase rail of its own and no longer part of
     FnVCCINT1's rail (DDC current-share / fault traffic). Refuses if N is
     FnVCCINT1's rail. Each VCCINT pair has its own DDC (SHARE) net, so no
     other LGA80D is checked.

Only FnVCCINT1 and FnVCCINT2 of the selected FPGA are read or written (plus
the MCU power state); the other FPGA's supplies are never accessed.
  4. Read everything back and compare.
The 2-phase step is idempotent: FnVCCINT1 already at phase IDs 0/1 with a
2-phase field is left as is, so --drop-rail can be added on a later run.

--restore undoes this without a VIN cycle, back to the strap configuration
(4-phase rail across FnVCCINT1 + FnVCCINT2):
  1. FnVCCINT1 and FnVCCINT2, both pages: DDC_CONFIG -> FnVCCINT1's rail ID
     (never changed by this script), phase IDs 0/2 and 1/3, 4-phase field.
     The 4-phase field is copied from FnVCCINT2 if it still holds the strap
     values (phase IDs 1 and 3 on FnVCCINT1's rail); if FnVCCINT2 was moved
     with --drop-rail it is FnVCCINT1's 2-phase field + 2. Anything else
     refuses.
  2. FnVCCINT2, both pages: ON_OFF_CONFIG = 0x17 (factory default, EN pin
     only) FIRST, then OPERATION = 0x80 (default; on/off bit now ignored).
     The reverse order would turn FnVCCINT2 on immediately.
  3. Read everything back and compare.
A VIN cycle of the LGA80Ds gives the same result.

The output must be on VSEN0P/N in 2-phase mode; on this board VS1 and VS2
are tied together and go to the output, so USER_GLOBAL_CONFIG is not touched.
USER_GLOBAL_CONFIG (E9h) is only READ and printed (before and after), so the
VSENSE-select bits set by the CFG strap can be inspected; the ZL8802 datasheet and
the LGA80D app note disagree on their meaning.

Does NOT touch the MCU. The MCU power state machine requires PG_Fn_INT_A and
PG_Fn_INT_B at level L1 and cannot ignore them, so with FnVCCINT2 disabled
the MCU is expected to report a power failure unless firmware/PG wiring
handles it.

Without --apply this only reads and prints the plan. With --apply it writes,
but still refuses unless the MCU power state machine reads POWER_OFF. Same for
--restore.

Usage:
    python -m cm_interface.tools.vccint_2phase --fpga 2        # read-only: show current state + plan
    python -m cm_interface.tools.vccint_2phase --fpga 2 --apply
    python -m cm_interface.tools.vccint_2phase --fpga 1        # read-only
    python -m cm_interface.tools.vccint_2phase --fpga 1 --apply
    python -m cm_interface.tools.vccint_2phase --fpga 1 --apply --drop-rail 20   # also move FnVCCINT2 to rail 20
    python -m cm_interface.tools.vccint_2phase --fpga 1 --restore            # read-only: restore plan
    python -m cm_interface.tools.vccint_2phase --fpga 1 --restore --apply
"""
import argparse
import os
import sys
import time

# Permit direct execution from a checkout (./tools/NAME.py) without the RPM.
if not __package__:
    _package_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.device.lga80d import LGA80DReg
from cm_interface.device.mcu import PowerFsmState
from cm_interface.registry import Registry

FPGAS = (1, 2)

DDC_CONFIG = 0xD3
USER_GLOBAL_CONFIG = 0xE9  # global, 2 bytes; read-only here
ON_OFF_OPERATION_ONLY = 0x1B  # bits 4:2 = 110, EN pin ignored; bits 1:0 = 11 (as default)
OPERATION_OFF = 0x00          # immediate off
ON_OFF_EN_ONLY = 0x17         # factory default: bits 4:2 = 101, EN pin only
OPERATION_DEFAULT = 0x80      # power-up value (CM3001/CM3019 dumps)
OPERATION_ON_OFF_BITS = 0xC0  # bits compared on read-back
# Strap 4-phase layout (CM3019 baseline): FnVCCINT1 pages = phase IDs 0, 2;
# FnVCCINT2 pages = phase IDs 1, 3.
KEEP_4PHASE_IDS = (0, 2)
DROP_4PHASE_IDS = (1, 3)
# 2-phase layout (TRN DDC_CONFIG): phase IDs 0, 1 on one module.
TWO_PHASE_IDS = (0, 1)
RAIL_IDS = range(32)  # DDC_CONFIG bits 12:8
PAGES = (0, 1)
# Datasheet: 5 ms between commands, 100 ms after STORE/RESTORE (unused here).
WRITE_GAP_S = 0.01


def fields(ddc):
    return {"phase_id": ddc >> 13, "rail": (ddc >> 8) & 0x1F,
            "phases": ddc & 0x7, "other": ddc & 0x00F8}


def read_ddc(lga, page):
    raw = lga.read_reg(lga._paged_reg(DDC_CONFIG, page), size=2)
    return int.from_bytes(raw, "little")


def rail_names(fpga):
    """Return (KEEP, DROP) LGA80D names for FPGA 1 or 2."""
    return "F%dVCCINT1" % fpga, "F%dVCCINT2" % fpga


def plan_ddc(keep, KEEP):
    """Return {page: new DDC_CONFIG} for the 2-phase rail, or raise.

    If KEEP is already in the 2-phase layout, new == current."""
    cur = {p: read_ddc(keep, p) for p in PAGES}
    f = {p: fields(cur[p]) for p in PAGES}
    if (all(f[p]["phase_id"] == TWO_PHASE_IDS[p] and f[p]["phases"] in (1, 2)
            and not f[p]["other"] for p in PAGES)
            and f[0]["rail"] == f[1]["rail"]
            and f[0]["phases"] == f[1]["phases"]):
        return cur, dict(cur)
    if f[0]["rail"] != f[1]["rail"]:
        raise RuntimeError("%s pages are on different rails (%d, %d); not a "
                           "single shared rail, refusing" %
                           (KEEP, f[0]["rail"], f[1]["rail"]))
    for p in PAGES:
        if f[p]["phases"] not in (3, 4):
            raise RuntimeError(
                "%s page %d DDC_CONFIG=0x%04X: phases field %d is not the "
                "4-phase value (3 or 4); refusing to guess" %
                (KEEP, p, cur[p], f[p]["phases"]))
        if f[p]["other"]:
            raise RuntimeError("%s page %d DDC_CONFIG=0x%04X has nonzero "
                               "reserved bits 7:3; refusing" % (KEEP, p, cur[p]))
    if f[0]["phases"] != f[1]["phases"]:
        raise RuntimeError("%s pages disagree on phases field" % KEEP)
    new = {}
    for p in PAGES:
        new[p] = (p << 13) | (f[p]["rail"] << 8) | (f[p]["phases"] - 2)
    return cur, new


def plan_drop_rail(drop, keep_rail, rail, phases):
    """Return ({page: current}, {page: new}) DDC_CONFIG moving DROP to its own
    2-phase rail `rail`, or raise. Each VCCINT pair has its own DDC (SHARE)
    net, so only the kept rail can collide; no other LGA80D is read."""
    if rail == keep_rail:
        raise RuntimeError("--drop-rail %d is the kept rail; refusing" % rail)
    cur = {p: read_ddc(drop, p) for p in PAGES}
    new = {p: (TWO_PHASE_IDS[p] << 13) | (rail << 8) | phases for p in PAGES}
    return cur, new


def plan_restore(keep, KEEP, drop, DROP):
    """Return (cur_keep, new_keep, cur_drop, new_drop) strap 4-phase
    DDC_CONFIG values, or raise. The rail ID is KEEP's (never changed)."""
    cur = {p: read_ddc(keep, p) for p in PAGES}
    ref = {p: read_ddc(drop, p) for p in PAGES}
    f = {p: fields(cur[p]) for p in PAGES}
    g = {p: fields(ref[p]) for p in PAGES}
    if f[0]["rail"] != f[1]["rail"] or f[0]["other"] or f[1]["other"]:
        raise RuntimeError("%s DDC_CONFIG 0x%04X/0x%04X: pages on different rails or "
                           "reserved bits set; refusing" % (KEEP, cur[0], cur[1]))
    rail = f[0]["rail"]
    drop_strap = all(g[p]["phase_id"] == DROP_4PHASE_IDS[p] and g[p]["rail"] == rail
                     and g[p]["phases"] in (3, 4) and not g[p]["other"]
                     and g[p]["phases"] == g[0]["phases"] for p in PAGES)
    drop_moved = all(g[p]["phase_id"] == TWO_PHASE_IDS[p] and g[p]["rail"] != rail
                     and g[p]["rail"] == g[0]["rail"] and not g[p]["other"]
                     and g[p]["phases"] == f[0]["phases"] for p in PAGES)
    drop_moved = drop_moved and f[0]["phases"] in (1, 2)
    if drop_strap:
        phases = g[0]["phases"]
    elif drop_moved:
        phases = f[0]["phases"] + 2
    else:
        raise RuntimeError(
            "%s DDC_CONFIG 0x%04X/0x%04X is neither the strap 4-phase layout "
            "(phase IDs 1/3 on rail %d) nor the --drop-rail 2-phase layout "
            "matching %s; refusing" % (DROP, ref[0], ref[1], rail, KEEP))
    for p in PAGES:
        if f[p]["phases"] not in (phases - 2, phases):
            raise RuntimeError(
                "%s page %d DDC_CONFIG=0x%04X: phases field %d is neither the "
                "2-phase (%d) nor the 4-phase (%d) value; refusing" %
                (KEEP, p, cur[p], f[p]["phases"], phases - 2, phases))
    new = {p: (KEEP_4PHASE_IDS[p] << 13) | (rail << 8) | phases for p in PAGES}
    new_drop = {p: (DROP_4PHASE_IDS[p] << 13) | (rail << 8) | phases for p in PAGES}
    return cur, new, ref, new_drop


def show_user_global_config(label, KEEP, keep_dev, DROP, drop_dev):
    """Print E9h (read-only). Bits 9:8 = VSENSE select; the ZL8802 datasheet and the
    LGA80D app note disagree on their meaning, so just report the raw value."""
    for name, lga in ((KEEP, keep_dev), (DROP, drop_dev)):
        raw = lga.read_reg(lga._paged_reg(USER_GLOBAL_CONFIG, 0), size=2)
        val = int.from_bytes(raw, "little")
        print("%s %s USER_GLOBAL_CONFIG (E9h) = 0x%04X  bits 9:8 (VSENSE select) = %d" % (
            label, name, val, (val >> 8) & 3))


def write_checked(lga, command, page, data):
    lga.write_reg(lga._paged_reg(command, page), data)
    time.sleep(WRITE_GAP_S)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fpga", type=int, choices=FPGAS, required=True,
                        help="FPGA rail to reconfigure: 1 = F1VCCINT, 2 = F2VCCINT")
    parser.add_argument("--apply", action="store_true",
                        help="perform the writes (default: read-only)")
    parser.add_argument("--restore", action="store_true",
                        help="go back to the strap 4-phase configuration "
                             "instead of 2-phase")
    parser.add_argument("--drop-rail", type=int, choices=RAIL_IDS, metavar="N",
                        help="2-phase mode only: also move FnVCCINT2 to its own "
                             "2-phase rail N (0-31, must be unused)")
    parser.add_argument("--dev-path", default="/dev/ttyUL4",
                        help="UART device (default: %(default)s)")
    args = parser.parse_args()
    if args.restore and args.drop_rail is not None:
        parser.error("--drop-rail cannot be combined with --restore")

    KEEP, DROP = rail_names(args.fpga)
    reg = Registry(dev_path=args.dev_path)
    keep = reg.get_lga80d(KEEP)
    drop = reg.get_lga80d(DROP)

    fsm = reg.get_mcu().read_power().fsm_state
    print("MCU power state machine: %s" % fsm.name)

    show_user_global_config("before:", KEEP, keep, DROP, drop)
    if args.restore:
        print("mode: restore strap 4-phase configuration")
        cur, new, cur_drop, new_drop = plan_restore(keep, KEEP, drop, DROP)
        onoff_new, op_new = ON_OFF_EN_ONLY, OPERATION_DEFAULT
    else:
        print("mode: 2-phase (%s only)" % KEEP)
        cur, new = plan_ddc(keep, KEEP)
        if args.drop_rail is not None:
            cur_drop, new_drop = plan_drop_rail(
                drop, fields(new[0])["rail"], args.drop_rail, new[0] & 0x7)
        else:
            cur_drop = {p: read_ddc(drop, p) for p in PAGES}
            new_drop = dict(cur_drop)
        onoff_new, op_new = ON_OFF_OPERATION_ONLY, OPERATION_OFF
    for p in PAGES:
        print("%s page %d DDC_CONFIG: 0x%04X -> 0x%04X" % (KEEP, p, cur[p], new[p]))
    for p in PAGES:
        print("%s page %d DDC_CONFIG: 0x%04X -> 0x%04X%s" % (
            DROP, p, cur_drop[p], new_drop[p],
            "" if new_drop[p] != cur_drop[p] else " (unchanged)"))
    for p in PAGES:
        print("%s page %d: ON_OFF_CONFIG=0x%02X OPERATION=0x%02X (current %02X/%02X)" % (
            DROP, p, onoff_new, op_new,
            drop.read_reg(drop._paged_reg(LGA80DReg.ON_OFF_CONFIG, p))[0],
            drop.read_reg(drop._paged_reg(LGA80DReg.OPERATION, p))[0]))

    if not args.apply:
        print("read-only: re-run with --apply to write")
        return 0
    if fsm != PowerFsmState.POWER_OFF:
        print("refusing to write: power state machine is %s, not POWER_OFF" % fsm.name)
        return 1

    # 2-phase: disable DROP before shrinking the rail. Restore: grow the rail
    # first, then hand DROP back to the EN pin. In both cases ON_OFF_CONFIG is
    # written before OPERATION.
    def write_drop():
        for p in PAGES:
            write_checked(drop, LGA80DReg.ON_OFF_CONFIG, p, bytes([onoff_new]))
            write_checked(drop, LGA80DReg.OPERATION, p, bytes([op_new]))

    def write_ddc():
        for lga, cur_v, new_v in ((drop, cur_drop, new_drop), (keep, cur, new)):
            for p in PAGES:
                if new_v[p] != cur_v[p]:
                    write_checked(lga, DDC_CONFIG, p, new_v[p].to_bytes(2, "little"))

    if args.restore:
        write_ddc()
        write_drop()
    else:
        write_drop()
        write_ddc()

    ok = True
    for p in PAGES:
        got = read_ddc(keep, p)
        got_drop = read_ddc(drop, p)
        onoff = drop.read_reg(drop._paged_reg(LGA80DReg.ON_OFF_CONFIG, p))[0]
        op = drop.read_reg(drop._paged_reg(LGA80DReg.OPERATION, p))[0]
        good = (got == new[p] and got_drop == new_drop[p] and onoff == onoff_new and
                (op & OPERATION_ON_OFF_BITS) == (op_new & OPERATION_ON_OFF_BITS))
        ok = ok and good
        print("%s page %d DDC_CONFIG=0x%04X | %s page %d DDC_CONFIG=0x%04X "
              "ON_OFF_CONFIG=0x%02X OPERATION=0x%02X  %s" % (
                  KEEP, p, got, DROP, p, got_drop, onoff, op,
                  "OK" if good else "MISMATCH"))
    show_user_global_config("after:", KEEP, keep, DROP, drop)
    if not ok:
        print("VERIFY FAILED")
    elif args.restore:
        print("restored strap 4-phase configuration")
    else:
        print("applied, NOT stored: reverts on LGA80D VIN cycle")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
