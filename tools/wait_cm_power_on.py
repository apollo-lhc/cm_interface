#!/usr/bin/env python3
"""Wait until the MCU power state machine is POWER_ON, for shell scripts.

Polls every --interval seconds. Read errors while waiting (e.g. the MCU busy
during power-up) are retried, not fatal.

Exit status: 0 = power on, 1 = timed out with power not on, 2 = timed out with
the state unreadable, or at once if --dev-path does not exist.
Prints one line at the end unless --quiet.

Usage:
    cm-wait-cm-power-on
    cm-wait-cm-power-on --timeout 300 -q && cm-clock-checks
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

from cm_interface.tools import is_cm_power_on
from cm_interface.tools._common import UNKNOWN, YES, add_common_args, add_quiet_arg, run_predicate


def wait(mcu, timeout, interval, clock=time.monotonic, sleep=time.sleep):
    """Poll is_cm_power_on.check until YES or timeout; return the last
    (exit status, message), with the wait time added."""
    start = clock()
    while True:
        status, message = is_cm_power_on.check(mcu)
        elapsed = clock() - start
        if status == YES:
            return status, "%s after %.0f s" % (message, elapsed)
        if elapsed >= timeout:
            return status, "%s after %.0f s (timed out)" % (message, elapsed)
        sleep(min(interval, max(0.0, timeout - elapsed)))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeout", type=float, default=60.0, metavar="S",
                        help="give up after S seconds (default: %(default)s)")
    parser.add_argument("--interval", type=float, default=1.0, metavar="S",
                        help="seconds between polls (default: %(default)s)")
    add_quiet_arg(parser)
    add_common_args(parser)
    args = parser.parse_args()
    # The port opens lazily, so a wrong path would otherwise poll to the timeout.
    if not os.path.exists(args.dev_path):
        if not args.quiet:
            print("unknown: %s does not exist" % args.dev_path, file=sys.stderr)
        return UNKNOWN
    return run_predicate(args, lambda mcu: wait(mcu, args.timeout, args.interval))


if __name__ == "__main__":
    sys.exit(main())
