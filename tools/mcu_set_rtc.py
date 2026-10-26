#!/usr/bin/env python3
"""Set the command-module MCU's RTC to this host's local time (``MC 0``).

Reads the RTC, writes the host's local wall-clock time through the Control
page (``MCU.set_rtc``), then reads it back and compares. The RTC has no time
zone. The time comes from whichever host runs this script (on the board, the
Zynq), so check that host's clock first: the MCU only refuses times before
2026-01-01.

Exits 1 if the write is refused, or if the read-back is invalid or more than
--tolerance seconds from the host clock.

Usage:
    cm-mcu-set-rtc
    cm-mcu-set-rtc --dry-run
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

from cm_interface.device.mcu import McuCapability, rtc_local_seconds
from cm_interface.errors import McuCapabilityUnavailable
from cm_interface.tools._common import add_common_args, make_registry


def rtc_datetime(rtc):
    """McuRtc as a naive datetime, or None when the RTC is not valid."""
    if not rtc.valid:
        return None
    return datetime.datetime(rtc.year, rtc.month, rtc.day,
                             rtc.hour, rtc.minute, rtc.second)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="read the RTC and show what would be written; write nothing")
    parser.add_argument("--tolerance", type=float, default=2.0, metavar="S",
                        help="maximum read-back offset from the host clock, seconds "
                             "(default: %(default)s)")
    add_common_args(parser)
    args = parser.parse_args()

    mcu = make_registry(args).get_mcu()
    print("MCU RTC before: %s" % mcu.read_runtime().rtc.isoformat())

    now = datetime.datetime.now().replace(microsecond=0)
    if args.dry_run:
        supported = bool(mcu.capabilities() & McuCapability.RTC_WRITE)
        print("would set:      %s (0x%08x)%s" % (
            now.isoformat(), rtc_local_seconds(now),
            "" if supported else "  -- firmware does not advertise RTC_WRITE"))
        return 0

    try:
        mcu.set_rtc(now)
    except McuCapabilityUnavailable as exc:
        print("error: %s (firmware older than map minor 4?)" % exc, file=sys.stderr)
        return 1
    except ValueError as exc:
        print("error: %s; is this host's clock set?" % exc, file=sys.stderr)
        return 1

    rtc = mcu.read_runtime().rtc
    print("MCU RTC after:  %s" % rtc.isoformat())
    after = rtc_datetime(rtc)
    if after is None:
        print("error: RTC reads back invalid", file=sys.stderr)
        return 1
    offset = (after - datetime.datetime.now()).total_seconds()
    print("offset vs host: %+.1f s" % offset)
    if abs(offset) > args.tolerance:
        print("error: offset exceeds %.1f s" % args.tolerance, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
