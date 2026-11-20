#!/usr/bin/env python3
"""Send PMBus CLEAR_FAULTS to the named LGA80D supplies.

WRITES. Sends CLEAR_FAULTS (03h, no data) to each supply, like the MCU CLI's
``psclr``. It is global on the LGA80D (both pages): it clears every fault bit
and releases SALRT. Faults whose condition persists re-assert immediately, so
STATUS_WORD for both pages is read back and printed afterwards. It does not
restart a supply that has shut down.

Clearing erases the latched fault evidence, so save it first with
``cm-lga80d-status NAME --out-dir DIR``. Works in any power state. Asks for
confirmation unless --yes. Needs MCU firmware that accepts a ProgCom DC write
with no data (cm_mcu #325).

Exits 1 if aborted or any supply failed.

Usage:
    cm-lga80d-clear-faults F1VCCINT1 F1VCCINT2
    cm-lga80d-clear-faults --all --yes
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
from cm_interface.tools._common import (add_common_args, add_device_args, make_registry,
                                        selected_devices)


def clear_faults(reg, names):
    """Clear each supply in turn; return True if any failed."""
    failed = False
    for name in names:
        lga = reg.get_lga80d(name)
        try:
            lga.clear_faults()
        except Exception as exc:  # keep going so one bad device doesn't hide the rest
            print("%s: FAILED: %s" % (name, exc))
            failed = True
            continue
        try:
            words = ["page %d STATUS_WORD 0x%04X" % (page, lga.read_status_word(page))
                     for page in (0, 1)]
            print("%s: CLEAR_FAULTS sent; %s" % (name, ", ".join(words)))
        except Exception as exc:
            print("%s: CLEAR_FAULTS sent; STATUS_WORD read-back FAILED: %s" % (name, exc))
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
    if not args.yes:
        print("WARNING: this clears the latched PMBus fault bits of: %s" % ", ".join(names))
        reply = input("Send CLEAR_FAULTS on live hardware? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return 1
    return 1 if clear_faults(reg, names) else 0


if __name__ == "__main__":
    sys.exit(main())
