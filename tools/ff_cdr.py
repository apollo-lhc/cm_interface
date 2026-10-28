#!/usr/bin/env python3
"""Report, enable or disable Firefly CDR (clock and data recovery) per channel.

Works on the populated Fireflies at the named locations (or --all), found the
same way as cm-ff-status: MCU present and user masks plus the board revision's
slot table. A location selects both halves of an x12 pair; --side limits it.

  * 25G x12 Tx/Rx: bytes 74-75 (Tables 22/45), channels 1-12.
  * x4: byte 98, Tx bits 7-4 and Rx bits 3-0, channels 1-4.
  * CERN-B and 14G x12 have no CDR control here (the MCU does not read CDR
    on them either) and are skipped.

Without --enable/--disable the enable state is only read. With either, each
module is read, changed and read back; the read-back is what is printed.

Usage:
    cm-ff-cdr --all
    cm-ff-cdr F1_1 --side rx --disable --channels 3,10
    cm-ff-cdr F2_6 --enable
"""
import argparse
import os
import sys

# Permit direct execution from a checkout (./tools/NAME.py) without the RPM.
if not __package__:
    _package_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.tools._common import (add_common_args, add_device_args, add_force_arg,
                                       make_registry, power_info, power_is_on,
                                       selected_devices)
from cm_interface.tools.ff_status import LOCATIONS, family, find_modules, parse_channels


def channel_list(channels):
    return ",".join(str(c) for c in channels) or "none"


def cdr_line(name, side, enabled, nchan):
    disabled = [ch for ch in range(1, nchan + 1) if ch not in enabled]
    state = "all enabled" if not disabled else "disabled ch %s" % channel_list(disabled)
    return "%-8s %-3s CDR %s" % (name.split()[0], side, state)


def handle(ff, name, kind, sides, args):
    """Apply the requested change, then return the report lines."""
    if kind == "4":
        tx, rx = "tx" in sides, "rx" in sides
        if args.enable:
            ff.enable_cdr(args.channels, tx=tx, rx=rx)
        elif args.disable:
            ff.disable_cdr(args.channels, tx=tx, rx=rx)
        status = ff.get_cdr_status()
        return [cdr_line(name, side.capitalize(),
                         [ch for ch in range(1, 5) if status[side] & (1 << (ch - 1))], 4)
                for side in ("tx", "rx") if side in sides]
    if args.enable:
        ff.enable_cdr(args.channels)
    elif args.disable:
        ff.disable_cdr(args.channels)
    mask = ff.get_cdr_enable()
    return [cdr_line(name, kind, [ch for ch in range(1, 13) if mask & (1 << (ch - 1))], 12)]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(parser, "Firefly location", LOCATIONS)
    parser.add_argument("--side", choices=("tx", "rx"),
                        help="only this side (default: both)")
    parser.add_argument("--channels", type=parse_channels, metavar="LIST",
                        help="channels to change, e.g. 1,3,5-8 (default: all)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--enable", action="store_true", help="enable CDR")
    action.add_argument("--disable", action="store_true", help="disable CDR")
    add_force_arg(parser)
    add_common_args(parser)
    args = parser.parse_args()
    wanted = selected_devices(parser, args, LOCATIONS)
    if args.all:
        wanted = None
    if args.channels and not (args.enable or args.disable):
        parser.error("--channels needs --enable or --disable")
    sides = (args.side,) if args.side else ("tx", "rx")

    reg = make_registry(args)
    _, power_state = power_info(reg)
    if not power_is_on(power_state, args.force):
        return 1
    try:
        _, notes, modules = find_modules(reg, wanted)
    except ValueError as exc:
        print(exc)
        return 1
    for note in notes:
        print(note)

    failed = False
    for _, name, kind, ff in modules:
        if kind != "4" and kind.lower() not in sides:
            continue
        try:
            if kind != "4":
                fam = family(ff.part_id)
                if fam != "25g":
                    print("%-8s %-3s skipped: %s part has no CDR control" % (
                        name.split()[0], kind, fam))
                    continue
            for line in handle(ff, name, kind, sides, args):
                print(line)
        except Exception as exc:  # keep going so one bad module doesn't hide the rest
            failed = True
            print("%-8s %-3s FAILED: %s" % (name.split()[0], "x4" if kind == "4" else kind,
                                             exc))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
