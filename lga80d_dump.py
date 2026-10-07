#!/usr/bin/env python3
"""Dump the readable PMBus registers of one LGA80D, both output pages.

READ ONLY: only read_reg() is called, except with --snapshot (see below). Register list mirrors
registers/lga80d.json. Skipped: write-only commands, PAGE (page is selected
per access), and block/string commands (MFR_ID, DDC_GROUP, passwords, ...; SNAPSHOT only via --snapshot)
and anything over 2 bytes (ASCR_CONFIG is 4), which the MCU firmware
can't read over PMBus (DATA_SIZE_ERROR).

Registers the JSON marks "unconfirmed" are skipped unless
--include-unconfirmed is given: the datasheet doesn't list them as supported,
and a read of an unsupported command may set the CML "invalid command" bit in
STATUS_CML on the device. STATUS_CML is therefore read first.

--snapshot also captures and prints the 32-byte SNAPSHOT (0xEA) per page through the
MCU's ProgCom "SN" device (LGA80D.read_snapshot). That is not a pure read: it makes the
MCU write SNAPSHOT_CONTROL=0x01 (copy the stored snapshot into the readable buffer; it
does not erase anything) and blocks the MCU's ProgCom task for ~70-400 ms. It needs firmware
with the SN device, so it is opt-in.

Usage:
    lga80d_dump.py F1VCCINT1
    lga80d_dump.py F1VCCINT1 --page 0 --include-unconfirmed
    lga80d_dump.py F1VCCINT1 --snapshot
"""
import argparse
import os
import sys
import time

# Permit direct execution from this package directory (as exercise_mcu.py does).
if not __package__:
    _package_dir = os.path.dirname(os.path.realpath(__file__))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.core_config import CORE_CONFIG
from cm_interface.registry import Registry
from cm_interface.utils import decode_linear11, decode_linear16u

# (name, command, size in bytes, unconfirmed). Mirrors registers/lga80d.json
# (documentation only, never loaded at runtime); STATUS_CML first, then address
# order, unconfirmed last.
MAX_READ_BYTES = 2

REGISTERS = [
    ("STATUS_CML", 0x7E, 1, False),
    ("OPERATION", 0x01, 1, False),
    ("ON_OFF_CONFIG", 0x02, 1, False),
    ("VOUT_MODE", 0x20, 1, False),
    ("VOUT_COMMAND", 0x21, 2, False),
    ("VOUT_TRIM", 0x22, 2, False),
    ("VOUT_CAL_OFFSET", 0x23, 2, False),
    ("VOUT_MAX", 0x24, 2, False),
    ("VOUT_MARGIN_HIGH", 0x25, 2, False),
    ("VOUT_MARGIN_LOW", 0x26, 2, False),
    ("VOUT_DROOP", 0x28, 2, False),
    ("FREQUENCY_SWITCH", 0x33, 2, False),
    ("INTERLEAVE", 0x37, 2, False),
    ("VOUT_OV_FAULT_LIMIT", 0x40, 2, False),
    ("VOUT_OV_FAULT_RESPONSE", 0x41, 1, False),
    ("VOUT_UV_FAULT_LIMIT", 0x44, 2, False),
    ("VOUT_UV_FAULT_RESPONSE", 0x45, 1, False),
    ("OT_FAULT_LIMIT", 0x4F, 2, False),
    ("OT_FAULT_RESPONSE", 0x50, 1, False),
    ("OT_WARN_LIMIT", 0x51, 2, False),
    ("VIN_OV_FAULT_LIMIT", 0x55, 2, False),
    ("VIN_OV_FAULT_RESPONSE", 0x56, 1, False),
    ("VIN_OV_WARN_LIMIT", 0x57, 2, False),
    ("VIN_UV_WARN_LIMIT", 0x58, 2, False),
    ("VIN_UV_FAULT_LIMIT", 0x59, 2, False),
    ("VIN_UV_FAULT_RESPONSE", 0x5A, 1, False),
    ("POWER_GOOD_ON", 0x5E, 2, False),
    ("TON_DELAY", 0x60, 2, False),
    ("TON_RISE", 0x61, 2, False),
    ("TOFF_DELAY", 0x64, 2, False),
    ("TOFF_FALL", 0x65, 2, False),
    ("STATUS_BYTE", 0x78, 1, False),
    ("STATUS_WORD", 0x79, 2, False),
    ("STATUS_VOUT", 0x7A, 1, False),
    ("STATUS_IOUT", 0x7B, 1, False),
    ("STATUS_INPUT", 0x7C, 1, False),
    ("STATUS_TEMP", 0x7D, 1, False),
    ("STATUS_MFR", 0x80, 1, False),
    ("READ_VIN", 0x88, 2, False),
    ("READ_VOUT", 0x8B, 2, False),
    ("READ_IOUT", 0x8C, 2, False),
    ("READ_TEMPERATURE_1", 0x8D, 2, False),
    ("READ_TEMPERATURE_3", 0x8F, 2, False),
    ("READ_DUTY", 0x94, 2, False),
    ("READ_FREQ", 0x95, 2, False),
    ("PMBUS_REVISION", 0x98, 1, False),
    ("ISENSE_CONFIG", 0xD0, 2, False),
    ("USER_CONFIG", 0xD1, 2, False),
    ("DDC_CONFIG", 0xD3, 2, False),
    ("POWER_GOOD_DELAY", 0xD4, 2, False),
    ("MULTI_PHASE_RAMP_GAIN", 0xD5, 1, False),
    ("SNAPSHOT_FAULT_MASK", 0xD7, 2, False),
    ("ASCR_CONFIG", 0xDF, 4, False),
    ("SEQUENCE", 0xE0, 2, False),
    ("MFR_IOUT_OC_FAULT_RESPONSE", 0xE5, 1, False),
    ("MFR_IOUT_UC_FAULT_RESPONSE", 0xE6, 1, False),
    ("IOUT_AVG_OC_FAULT_LIMIT", 0xE7, 2, False),
    ("USER_GLOBAL_CONFIG", 0xE9, 2, False),
    ("SNAPSHOT_CONTROL", 0xF3, 1, False),
    ("MFR_VMON_OV_FAULT_LIMIT", 0xF5, 2, False),
    ("MFR_VMON_UV_FAULT_LIMIT", 0xF6, 2, False),
    ("MFR_READ_VMON", 0xF7, 2, False),
    ("VMON_OV_FAULT_RESPONSE", 0xF8, 1, False),
    ("VMON_UV_FAULT_RESPONSE", 0xF9, 1, False),
    ("SECURITY_LEVEL", 0xFA, 1, False),
    ("PHASE", 0x04, 1, True),
    ("IOUT_COMMAND", 0x39, 2, True),
    ("IOUT_MAX", 0x3B, 2, True),
    ("IOUT_OC_FAULT_LIMIT", 0x46, 2, True),
    ("READ_IIN", 0x89, 2, True),
    ("READ_POUT", 0x96, 2, True),
    ("READ_PIN", 0x97, 2, True),
    ("MFR_VIN_MIN", 0xA0, 2, True),
    ("MFR_VIN_MAX", 0xA1, 2, True),
    ("MFR_VOUT_MIN", 0xA2, 2, True),
    ("MFR_VOUT_MAX", 0xA3, 2, True),
    ("MFR_IOUT_MAX", 0xA4, 2, True),
    ("USER_DATA", 0xB1, 4, True),
]

# name -> (decoder, unit). Anything not listed is printed raw only.
LINEAR16 = {"VOUT_COMMAND", "VOUT_MAX", "VOUT_MARGIN_HIGH", "VOUT_MARGIN_LOW",
            "VOUT_OV_FAULT_LIMIT", "VOUT_UV_FAULT_LIMIT", "READ_VOUT",
            "MFR_VOUT_MIN", "MFR_VOUT_MAX", "POWER_GOOD_ON"}
LINEAR11 = {
    "VIN_OV_WARN_LIMIT": "V", "VIN_UV_WARN_LIMIT": "V", "VIN_UV_FAULT_LIMIT": "V",
    "READ_VIN": "V", "READ_IOUT": "A", "READ_IIN": "A", "READ_PIN": "W",
    "READ_POUT": "W", "READ_TEMPERATURE_1": "C", "READ_TEMPERATURE_3": "C",
    "READ_DUTY": "%", "READ_FREQ": "kHz", "FREQUENCY_SWITCH": "kHz",
    "IOUT_COMMAND": "A", "IOUT_MAX": "A", "IOUT_OC_FAULT_LIMIT": "A",
    "IOUT_AVG_OC_FAULT_LIMIT": "A", "MFR_VIN_MIN": "V", "MFR_VIN_MAX": "V",
    "MFR_IOUT_MAX": "A", "TON_DELAY": "ms", "TON_RISE": "ms",
    "TOFF_DELAY": "ms", "TOFF_FALL": "ms", "VOUT_DROOP": "mV/A",
    "OT_FAULT_LIMIT": "C", "OT_WARN_LIMIT": "C", "VIN_OV_FAULT_LIMIT": "V",
    "POWER_GOOD_DELAY": "ms", "MFR_VMON_OV_FAULT_LIMIT": "V",
    "MFR_VMON_UV_FAULT_LIMIT": "V", "MFR_READ_VMON": "V",
}


def load_registers(include_unconfirmed):
    # The MCU firmware's PMBus read path (SMBusMasterByteWordRead) rejects reads
    # of more than 2 bytes with DATA_SIZE_ERROR.
    return [r for r in REGISTERS
            if r[2] <= MAX_READ_BYTES and (include_unconfirmed or not r[3])]


def decoded(name, raw):
    if name == "VOUT_MODE":
        mode, exponent = raw[0] >> 5, raw[0] & 0x1F
        if exponent & 0x10:
            exponent -= 0x20
        return "%s mode, exponent %d" % ("linear" if mode == 0 else "mode %d" % mode, exponent)
    if name in LINEAR16:
        return "%.4f V" % decode_linear16u(raw)
    if name in LINEAR11:
        return "%.4g %s" % (decode_linear11(raw), LINEAR11[name])
    return ""


def snapshot_lines(lga, page):
    """Capture and format the SNAPSHOT of one page. Returns (lines, ok);
    never raises, so one failure does not hide the rest of the dump."""
    lines = ["", "  SNAPSHOT (0xEA, captured via MCU 'SN')"]
    try:
        snap = lga.read_snapshot(page)
    except Exception as exc:
        lines.append("  %-28s READ FAILED: %s" % ("SNAPSHOT", exc))
        return lines, False
    lines.extend(snap.format_lines())
    return lines, True


def board_header(reg, args):
    """Return (header lines, board id or None). Never fatal: old firmware or a
    missing MCU capability just yields 'unavailable'."""
    lines = [
        "LGA80D register dump  %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "  supply:    %s (index 0x%02X)" % (args.device, CORE_CONFIG.lga80d[args.device]),
        "  uart:      %s" % args.dev_path,
    ]
    try:
        info = reg.get_mcu().system_info
    except Exception as exc:
        lines.append("  board id:  unavailable (%s)" % exc)
        return lines, None
    lines.append("  board id:  %d" % info.board_id)
    lines.append("  hw rev:    %d" % info.hardware_revision)
    lines.append("  firmware:  %s" % info.git_version)
    return lines, info.board_id


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("device", choices=sorted(CORE_CONFIG.lga80d),
                        help="LGA80D supply name")
    parser.add_argument("--page", type=int, choices=(0, 1),
                        help="only this output page (default: both)")
    parser.add_argument("--include-unconfirmed", action="store_true",
                        help="also read registers the datasheet doesn't list as supported")
    parser.add_argument("--snapshot", action="store_true",
                        help="also capture and print the 32-byte SNAPSHOT per page "
                             "(not a pure read; needs firmware with the SN device)")
    parser.add_argument("--dev-path", default="/dev/ttyUL4",
                        help="UART device (default: %(default)s)")
    parser.add_argument("--out-dir", metavar="DIR",
                        help="also write one file per page to DIR, named "
                             "cm<boardid>_<supply>_<page>.txt (existing files are "
                             "overwritten; board id is 'unknown' if the MCU can't "
                             "report it)")
    args = parser.parse_args()

    reg = Registry(dev_path=args.dev_path)
    lga = reg.get_lga80d(args.device)
    header, board_id = board_header(reg, args)
    registers = load_registers(args.include_unconfirmed)
    pages = (0, 1) if args.page is None else (args.page,)
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
    failed = False
    for page in pages:
        lines = [
            "",
            "== %s  page %d ==" % (args.device, page),
            "  %-28s %-4s  %-10s  %s" % ("REGISTER", "CMD", "RAW", "DECODED"),
            "  %s" % ("-" * 66),
        ]
        for name, cmd, size, unconfirmed in registers:
            try:
                raw = lga.read_reg(lga._paged_reg(cmd, page), size=size)
            except Exception as exc:  # keep going; one bad register shouldn't hide the rest
                failed = True
                lines.append("  %-28s 0x%02X  READ FAILED: %s" % (name, cmd, exc))
                continue
            value = int.from_bytes(raw, "little")
            lines.append("  %-28s 0x%02X  %-10s  %s%s" % (
                name, cmd, "0x%0*X" % (size * 2, value), decoded(name, raw),
                "  (unconfirmed)" if unconfirmed else ""))
        if args.snapshot:
            snap_lines, snap_ok = snapshot_lines(lga, page)
            lines.extend(snap_lines)
            failed = failed or not snap_ok
        print("\n".join((header if page == pages[0] else []) + lines))
        if args.out_dir:
            # Every file carries the header, even for the second page.
            path = os.path.join(args.out_dir, "cm%s_%s_%d.txt" % (
                "unknown" if board_id is None else board_id, args.device, page))
            with open(path, "w") as fh:
                fh.write("\n".join(header + lines) + "\n")
            print("wrote %s" % path)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
