#!/usr/bin/env python3
"""Reset the LGA80D SNAPSHOT history (both pages) on the named supplies.

WRITES. For each supply this captures the snapshot (SNAPSHOT_CONTROL=0x01)
and then resets it (0x03), like the MCU CLI's ``sn_all``. The reset erases the
stored fault history, so save it first with
``cm-lga80d-dump NAME --snapshot --out-dir DIR``.

The reset only succeeds while the supply output is off, so this refuses unless
the MCU power state machine reads POWER_OFF (checked again before each supply;
there is no override). Asks for confirmation unless --yes.

Exits 1 if refused, aborted, or any supply failed.

Usage:
    cm-lga80d-snapshot-reset F1VCCINT1 F1VCCINT2
    cm-lga80d-snapshot-reset --all --yes
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

from cm_interface.core_config import CORE_CONFIG
from cm_interface.device.mcu import PowerFsmState
from cm_interface.tools._common import (add_common_args, add_device_args, make_registry,
                                        power_info, selected_devices)


def reset_snapshots(reg, names):
    """Reset each supply in turn; return True if any failed. The power check
    lives in Registry.reset_all_lga80d_snapshots and runs per supply."""
    failed = False
    for name in names:
        try:
            reg.reset_all_lga80d_snapshots(force=True, names=[name])
            # An ACK does not prove the erase: the device ignores the reset
            # while its output is on (firmware LocalTasks.c snapreset()).
            print("%s: reset written (pages 0, 1)" % name)
        except Exception as exc:  # keep going so one bad device doesn't hide the rest
            print("%s: FAILED: %s" % (name, exc))
            failed = True
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(parser, "LGA80D supply", sorted(CORE_CONFIG.lga80d))
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    add_common_args(parser)
    args = parser.parse_args()
    names = selected_devices(parser, args, sorted(CORE_CONFIG.lga80d))

    reg = make_registry(args)
    power_text, state = power_info(reg)
    if state != PowerFsmState.POWER_OFF:
        print("refusing: MCU power state is %s, not POWER_OFF; the snapshot reset "
              "only works with the supplies off" % power_text)
        return 1
    if not args.yes:
        print("WARNING: this erases the stored SNAPSHOT fault history of: %s"
              % ", ".join(names))
        reply = input("Reset the snapshots on live hardware? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return 1
    return 1 if reset_snapshots(reg, names) else 0


if __name__ == "__main__":
    sys.exit(main())
