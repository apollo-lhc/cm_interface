#!/usr/bin/env python3
"""Exit 0 if the MCU power state machine is POWER_ON, for shell scripts.

Exit status: 0 = power on, 1 = any other state, 2 = unknown (MCU unreachable,
state unreadable, bad arguments). Prints one line unless --quiet.

Usage:
    cm-is-cm-power-on
    if cm-is-cm-power-on -q; then cm-clock-checks; fi
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

from cm_interface.device.mcu import PowerFsmState
from cm_interface.tools._common import (NO, UNKNOWN, YES, add_common_args,
                                        add_quiet_arg, run_predicate)


def check(mcu):
    """Return (exit status, message)."""
    try:
        state = mcu.read_power().fsm_state
    except Exception as exc:
        return UNKNOWN, "unknown: MCU power state unreadable (%s)" % exc
    if state == PowerFsmState.POWER_ON:
        return YES, "yes: %s" % state.name
    return NO, "no: %s" % state.name


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_quiet_arg(parser)
    add_common_args(parser)
    return run_predicate(parser.parse_args(), check)


if __name__ == "__main__":
    sys.exit(main())
