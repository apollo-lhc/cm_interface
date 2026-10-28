#!/usr/bin/env python3
"""Report or set Firefly Rx output amplitude and pre-emphasis per channel.

Works on the populated Rx-capable Fireflies at the named locations (or --all),
found the same way as cm-ff-status. Values are the raw 4-bit codes; the
report shows what each code means.

  * 25G x12 Rx: swing bytes 62-67 (Table 43) and pre-emphasis bytes 68-73
    (Table 44). Swing is 80 mV + 76 mV per step, codes 7-15 = 612 mV
    (default 4 = 384 mV); pre-emphasis is 4.8 % of amplitude per step, codes
    7-15 = 33.6 % (default 2 = 9.6 %). The first byte's high nibble is
    channel 12, the last byte's low nibble channel 1.
  * x4: upper page 03h bytes 238-239 amplitude (Table 49, codes 0-3 =
    300/450/600/900 mV) and 236-237 de-emphasis (Table 48, codes 0-7 =
    0-6 dB, 7.5 dB). The datasheet calls the x4 amplitude a global parameter,
    so per-channel amplitude settings may not take effect independently.
  * CERN-B and 14G x12 are skipped (not checked against their manuals).

With --amplitude/--preemphasis, each module is read, changed and read back;
the read-back is what is printed.

Usage:
    cm-ff-rx-eq --all
    cm-ff-rx-eq F1_1 --amplitude 5 --channels 1-4
    cm-ff-rx-eq F2_6 --preemphasis 3
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


def int_code(text):
    try:
        return int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError("bad code %r" % text)


def handle(ff, args):
    """Apply the requested changes, then return the report lines."""
    if args.amplitude is not None:
        ff.set_output_amplitude(args.amplitude, args.channels)
    if args.preemphasis is not None:
        ff.set_output_preemphasis(args.preemphasis, args.channels)
    amp = ff.get_output_amplitude()
    pre = ff.get_output_preemphasis()
    lines = ["  %3s  %-14s %s" % ("ch", "amplitude", "pre-emphasis")]
    for ch in sorted(amp):
        lines.append("  %3d  0x%X %-10s 0x%X %s" % (
            ch, amp[ch], ff.AMPLITUDE_CODES.get(amp[ch], "reserved"),
            pre[ch], ff.PREEMPHASIS_CODES.get(pre[ch], "reserved")))
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(parser, "Firefly location", LOCATIONS)
    parser.add_argument("--amplitude", type=int_code, metavar="CODE",
                        help="set the output amplitude code (x12: 0-15, x4: 0-3)")
    parser.add_argument("--preemphasis", type=int_code, metavar="CODE",
                        help="set the output pre-emphasis code (x12: 0-15, x4: 0-7)")
    parser.add_argument("--channels", type=parse_channels, metavar="LIST",
                        help="channels to change, e.g. 1,3,5-8 (default: all)")
    add_force_arg(parser)
    add_common_args(parser)
    args = parser.parse_args()
    wanted = selected_devices(parser, args, LOCATIONS)
    if args.all:
        wanted = None
    if args.channels and args.amplitude is None and args.preemphasis is None:
        parser.error("--channels needs --amplitude or --preemphasis")

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
        if kind == "Tx":
            continue
        label = "%-8s %s" % (name.split()[0], "x4" if kind == "4" else "Rx")
        try:
            if kind == "Rx":
                fam = family(ff.part_id)
                if fam != "25g":
                    print("%s skipped: %s part not supported" % (label, fam))
                    continue
            lines = handle(ff, args)
        except Exception as exc:  # keep going so one bad module doesn't hide the rest
            failed = True
            print("%s FAILED: %s" % (label, exc))
            continue
        print(label)
        print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
