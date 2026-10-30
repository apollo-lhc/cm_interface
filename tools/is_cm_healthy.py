#!/usr/bin/env python3
"""Exit 0 if the MCU reports no faults or alarms, for shell scripts.

Unhealthy means any of: a System-page health flag set, POWER_FAULT_LATCH or
ALARM_SHUTDOWN_LATCH set, or the power state machine in POWER_FAILURE. Power
being off is not a fault. A device above its temperature threshold right now
(warn level or worse) sets the TEMPERATURE_ALARM health flag and so counts; the
temperature warn latch, which only holds a warning while the device cools
through the hysteresis band, is reported but does not count.

Exit status: 0 = healthy, 1 = not, 2 = unknown (MCU unreachable, pages
unreadable, bad arguments). Prints one line unless --quiet.

Usage:
    cm-is-cm-healthy
    cm-is-cm-healthy -q || cm-mcu-dump --out-dir faults/
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

from cm_interface.device.mcu import PowerFlags, PowerFsmState
from cm_interface.tools._common import (NO, YES, add_common_args, add_quiet_arg,
                                        flag_names, run_predicate)

LATCHES = PowerFlags.POWER_FAULT_LATCH | PowerFlags.ALARM_SHUTDOWN_LATCH


def check(mcu):
    """Return (exit status, message)."""
    health = mcu.system_info.health
    power = mcu.read_power()
    problems = ["health %s" % n for n in flag_names(health)]
    problems += ["latched %s" % n for n in flag_names(power.flags & LATCHES)]
    if power.fsm_state == PowerFsmState.POWER_FAILURE:
        problems.append("power %s" % power.fsm_state.name)
    # Informational only; an Alarm page this firmware lacks is not a problem.
    try:
        warned = flag_names(mcu.read_alarm().temp_warn_latch)
    except Exception:
        warned = []
    note = "  (temp warn latch: %s)" % ", ".join(warned) if warned else ""
    if problems:
        return NO, "no: %s%s" % ("; ".join(problems), note)
    return YES, "yes: power %s%s" % (power.fsm_state.name, note)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_quiet_arg(parser)
    add_common_args(parser)
    return run_predicate(parser.parse_args(), check)


if __name__ == "__main__":
    sys.exit(main())
