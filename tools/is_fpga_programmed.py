#!/usr/bin/env python3
"""Exit 0 if the FPGA(s) are configured (DONE asserted), for shell scripts.

Checks F1 and F2 by default, or the one named. The answer is "no" unless the
MCU power state machine is POWER_ON: an unpowered FPGA's DONE pin means
nothing.

Exit status: 0 = every selected FPGA done, 1 = not, 2 = unknown (MCU
unreachable, state unreadable, bad arguments). Prints one line unless --quiet.

Usage:
    cm-is-fpga-programmed
    cm-is-fpga-programmed f2
    if cm-is-fpga-programmed -q f1; then ...; fi
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

from cm_interface.device.mcu import FpgaDone, PowerFsmState
from cm_interface.tools._common import (NO, YES, add_common_args, add_quiet_arg,
                                        run_predicate)

FPGAS = {"f1": FpgaDone.F1, "f2": FpgaDone.F2}


def check(mcu, names=("f1", "f2")):
    """Return (exit status, message) for the FPGAs in names."""
    state = mcu.read_power().fsm_state
    if state != PowerFsmState.POWER_ON:
        return NO, "no: power is %s" % state.name
    done = mcu.read_runtime().fpga_done
    missing = [n.upper() for n in names if not done & FPGAS[n]]
    if missing:
        return NO, "no: %s not done" % ", ".join(missing)
    return YES, "yes: %s done" % ", ".join(n.upper() for n in names)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fpga", nargs="?", type=str.lower, choices=sorted(FPGAS),
                        help="FPGA to check, any case (default: both)")
    add_quiet_arg(parser)
    add_common_args(parser)
    args = parser.parse_args()
    names = (args.fpga,) if args.fpga else tuple(sorted(FPGAS))
    return run_predicate(args, lambda mcu: check(mcu, names))


if __name__ == "__main__":
    sys.exit(main())
