#!/usr/bin/env python3
"""Exit 0 if the MCU's RTC is valid and close to this host's clock.

"Close" is within --tolerance seconds of this host's local wall-clock time,
the time cm-mcu-set-rtc writes. Check this host's clock first.

Exit status: 0 = set, 1 = invalid or off by more than --tolerance, 2 =
unknown (MCU unreachable, Runtime page unreadable, bad arguments). Prints one
line unless --quiet.

Usage:
    cm-is-rtc-set
    cm-is-rtc-set -q || cm-mcu-set-rtc
"""
import argparse
import datetime
import os
import sys

# Permit direct execution from a checkout (./tools/NAME.py) without the RPM.
if not __package__:
    _package_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.tools._common import (NO, YES, add_common_args, add_quiet_arg,
                                        run_predicate)
from cm_interface.tools.mcu_set_rtc import rtc_datetime


def check(mcu, tolerance, now=datetime.datetime.now):
    """Return (exit status, message)."""
    when = rtc_datetime(mcu.read_runtime().rtc)
    if when is None:
        return NO, "no: RTC not valid"
    offset = (when - now()).total_seconds()
    text = "%s (%+.0f s vs host)" % (when.isoformat(), offset)
    if abs(offset) > tolerance:
        return NO, "no: %s, more than %g s off" % (text, tolerance)
    return YES, "yes: %s" % text


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tolerance", type=float, default=60.0, metavar="S",
                        help="maximum offset from this host's clock, seconds "
                             "(default: %(default)s)")
    add_quiet_arg(parser)
    add_common_args(parser)
    args = parser.parse_args()
    return run_predicate(args, lambda mcu: check(mcu, args.tolerance))


if __name__ == "__main__":
    sys.exit(main())
