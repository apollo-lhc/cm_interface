#!/usr/bin/env python3
"""Print the PMBus status registers of LGA80D DC-DC converters, decoded.

READ ONLY: only LGA80D.read_status() is called (PMBus reads of STATUS_WORD,
STATUS_VOUT/IOUT/INPUT/TEMP/CML/MFR_SPECIFIC). Nothing is written, cleared
or reconfigured.

Bit meanings are from LGA80D_40A_dual_AppNote.pdf, "STATUS_*" command pages.

Usage:
    cm-lga80d-status F1VCCINT1        # one device, both output pages
    cm-lga80d-status --all            # every LGA80D on the board
    cm-lga80d-status --all --out-dir DIR
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

from cm_interface.core_config import CORE_CONFIG
from cm_interface.tools._common import (add_common_args, add_device_args, add_out_dir_arg,
                                       board_info, make_registry, out_path,
                                       selected_devices, state_lines, write_lines)

# bit number -> meaning. Bits missing from a table are "Not Used" in the
# datasheet; if one is set it is printed as undocumented.
STATUS_WORD_BITS = {
    15: "VOUT: output voltage fault or warning",
    14: "IOUT: output current fault",
    13: "INPUT: input voltage fault or warning",
    12: "MFR_SPECIFIC: manufacturer-specific fault or warning",
    11: "POWER_GOOD#: power-good negated (signal state, not a fault by itself)",
    9: "OTHER: a bit is set in a STATUS_VOUT/IOUT/INPUT/TEMP/CML/MFR register",
    7: "BUSY: device was busy and unable to respond",
    6: "OFF: output not providing power for any reason, incl. not enabled "
       "(not a fault by itself)",
    5: "VOUT_OV_FAULT: output overvoltage fault",
    4: "VOUT_OC_FAULT: output overcurrent fault",
    3: "VIN_UV_FAULT: input undervoltage fault",
    2: "TEMPERATURE: temperature fault or warning",
    1: "CML: communications, memory or logic fault",
    0: "None of the above: other fault, see bits 15:8",
}
STATUS_VOUT_BITS = {
    7: "VOUT_OV_FAULT: output overvoltage fault",
    4: "VOUT_UV_FAULT: output undervoltage fault",
}
STATUS_IOUT_BITS = {
    7: "IOUT_OC_FAULT: output overcurrent fault",
    4: "IOUT_UC_FAULT: output undercurrent fault",
}
STATUS_INPUT_BITS = {
    7: "VIN_OV_FAULT: input overvoltage fault",
    6: "VIN_OV_WARNING: input overvoltage warning",
    5: "VIN_UV_WARNING: input undervoltage warning",
    4: "VIN_UV_FAULT: input undervoltage fault",
}
STATUS_TEMP_BITS = {
    7: "OT_FAULT: over-temperature fault",
    6: "OT_WARNING: over-temperature warning",
    5: "UT_WARNING: under-temperature warning",
    4: "UT_FAULT: under-temperature fault (datasheet table labels it UV_FAULT)",
}
STATUS_CML_BITS = {
    7: "invalid or unsupported PMBus command received",
    6: "PMBus command sent with invalid or unsupported data",
    5: "packet error detected in PMBus command",
    1: "write to read-only/protected command, or other communication fault",
}
STATUS_MFR_BITS = {
    6: "DDC warning: error detected on the DDC bus",
    5: "VMON UV warning: VMON 10% below MFR_VMON_UV_FAULT level",
    4: "VMON OV warning: VMON 10% above MFR_VMON_OV_FAULT level",
    3: "External switching period fault: loss of external clock sync",
    1: "VMON UV fault: VMON below MFR_VMON_UV_FAULT level",
    0: "VMON OV fault: VMON above MFR_VMON_OV_FAULT level",
}

# (title, LGA80DStatus attribute, bit table, width in bits)
REGISTERS = [
    ("STATUS_WORD (0x79)",         "word",         STATUS_WORD_BITS,  16),
    ("STATUS_VOUT (0x7A)",         "vout",         STATUS_VOUT_BITS,   8),
    ("STATUS_IOUT (0x7B)",         "iout",         STATUS_IOUT_BITS,   8),
    ("STATUS_INPUT (0x7C)",        "input",        STATUS_INPUT_BITS,  8),
    ("STATUS_TEMP (0x7D)",         "temperature",  STATUS_TEMP_BITS,   8),
    ("STATUS_CML (0x7E)",          "cml",          STATUS_CML_BITS,    8),
    ("STATUS_MFR_SPECIFIC (0x80)", "manufacturer", STATUS_MFR_BITS,    8),
]


def status_lines(name, page, status):
    lines = ["%s  page %d  has_faults=%s" % (name, page, status.has_faults)]
    for title, attr, bits, width in REGISTERS:
        value = getattr(status, attr)
        lines.append("  %-27s = 0x%0*X" % (title, width // 4, value))
        for bit in range(width - 1, -1, -1):
            if value & (1 << bit):
                lines.append("      bit %2d: %s" % (bit, bits.get(bit, "undocumented bit")))
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(parser, "LGA80D supply", sorted(CORE_CONFIG.lga80d))
    add_out_dir_arg(parser, "cm<boardid>_<supply>_status.txt, one file per supply")
    add_common_args(parser)
    args = parser.parse_args()
    names = selected_devices(parser, args, sorted(CORE_CONFIG.lga80d))

    reg = make_registry(args)
    board_lines, board_id = board_info(reg)
    board_lines = board_lines + state_lines(reg)
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
    failed = False
    for name in names:
        lga = reg.get_lga80d(name)
        lines = [
            "LGA80D status  %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "  supply:    %s (index 0x%02X)" % (name, CORE_CONFIG.lga80d[name]),
            "  uart:      %s" % args.dev_path,
        ] + board_lines
        supply_failed = False
        for page in (0, 1):
            try:
                lines += status_lines(name, page, lga.read_status(page))
            except Exception as exc:  # keep going so one bad device doesn't hide the rest
                supply_failed = True
                lines.append("%s  page %d  READ FAILED: %s" % (name, page, exc))
        failed = failed or supply_failed
        if args.out_dir:
            write_lines(out_path(args.out_dir, board_id, name + "_status"), lines,
                        supply_failed)
        else:
            print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
