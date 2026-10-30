#!/usr/bin/env python3
"""Print a status summary of the populated Fireflies (all, or the locations named).

"Populated" is the MCU's Firefly present mask (system page 0x58, built from
the *_PRESENT pins at MCU boot). A present module that is not also in the
user mask (0x54) is listed but not read: the MCU refuses ProgCom access to it.

For each module read: part ID, serial (x12 only), case temperature, Vcc3.3,
status/summary bytes and the latched alarm bytes, decoded. Register addresses
are from the ECUO 25G x12 Rev 4, CERN-B x12 and ECUO 25G/28G x4 datasheets.

Nothing is written. Reading a latched alarm byte clears it, but the MCU
already polls these same bytes continuously (MON_I2C_rev*.yml), so this adds
no new side effect; it also means a latched bit shown here is usually a
condition that is still present (it re-latches at once), not history.

Usage:
    cm-ff-status --all
    cm-ff-status F1_1 F2_6
    cm-ff-status --all --out-dir DIR
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

from cm_interface import __version__
from cm_interface.device.firefly import Firefly4, FireflyReg, FireflyRx, FireflyTx
from cm_interface.tools._common import (add_common_args, add_device_args, add_force_arg,
                                       add_out_dir_arg, fpga_done_info, make_registry, out_path,
                                       power_info, power_is_on, selected_devices, write_lines)

# Firmware ff_moni2c_addrs[] (cm_mcu LocalTasks.c) by board revision; the
# device number is the index. Kind: "Tx"/"Rx" = x12 half, "4" = x4 XCVR.
SLOTS = {
    2: (("F1_1 Tx", "Tx"), ("F1_1 Rx", "Rx"), ("F1_2 Tx", "Tx"),
        ("F1_2 Rx", "Rx"), ("F1_3 Tx", "Tx"), ("F1_3 Rx", "Rx"),
        ("F1_4", "4"), ("F1_5", "4"), ("F1_6", "4"), ("F1_7", "4"),
        ("F2_1 Tx", "Tx"), ("F2_1 Rx", "Rx"), ("F2_2 Tx", "Tx"),
        ("F2_2 Rx", "Rx"), ("F2_3 Tx", "Tx"), ("F2_3 Rx", "Rx"),
        ("F2_4", "4"), ("F2_5", "4"), ("F2_6", "4"), ("F2_7", "4")),
    3: (("F1_1 Tx", "Tx"), ("F1_1 Rx", "Rx"), ("F1_2 Tx", "Tx"),
        ("F1_2 Rx", "Rx"), ("F1_3 Tx", "Tx"), ("F1_3 Rx", "Rx"),
        ("F1_4 Tx", "Tx"), ("F1_4 Rx", "Rx"), ("F1_5", "4"), ("F1_6", "4"),
        ("F2_1 Tx", "Tx"), ("F2_1 Rx", "Rx"), ("F2_2 Tx", "Tx"),
        ("F2_2 Rx", "Rx"), ("F2_3 Tx", "Tx"), ("F2_3 Rx", "Rx"),
        ("F2_4 Tx", "Tx"), ("F2_4 Rx", "Rx"), ("F2_5", "4"), ("F2_6", "4")),
}
CLASSES = {"Tx": FireflyTx, "Rx": FireflyRx, "4": Firefly4}


def location(slot_name):
    """'F1_1 Tx' -> 'F1_1': a location selects both halves of an x12 pair."""
    return slot_name.split()[0]


# Every location on any revision, for --help and name checking before the MCU
# says which revision this board is.
LOCATIONS = sorted({location(name) for slots in SLOTS.values() for name, _ in slots})

def select_slots(slots, populated, wanted, hw_rev):
    """Return (note lines, populated slot numbers to read). wanted is None for
    --all, meaning every populated slot of this revision; otherwise the named
    locations, and only those are reported when absent or not populated."""
    if wanted is None:
        return [], populated
    rev_locations = [location(name) for name, _ in slots]
    notes = []
    for loc in wanted:
        if loc not in rev_locations:
            notes.append("%s: no such location on board revision %d" % (loc, hw_rev))
        elif not any(location(slots[n][0]) == loc for n in populated):
            notes.append("%s: not populated" % loc)
    return notes, [n for n in populated if location(slots[n][0]) in wanted]


def find_modules(reg, wanted):
    """Return (system info, note lines, [(slot number, slot name, kind, device)])
    for the populated modules at the wanted locations (None = all of this
    board revision's slots). Modules not in the MCU user mask get a note
    instead of a device. Raises ValueError if the MCU can't say what is
    fitted."""
    info = reg.mcu.system_info
    if info.ff_present_mask is None:
        raise ValueError("MCU map minor %d has no Firefly masks (needs >= 1)"
                         % info.map_minor)
    slots = SLOTS.get(info.hardware_revision)
    if slots is None:
        raise ValueError("no Firefly slot table for board revision %d"
                         % info.hardware_revision)
    populated = [n for n in range(len(slots)) if info.ff_present_mask & (1 << n)]
    notes, to_read = select_slots(slots, populated, wanted, info.hardware_revision)
    modules = []
    for n in to_read:
        name, kind = slots[n]
        if not info.ff_user_mask & (1 << n):
            notes.append("%s: present but not in user mask; MCU will not access it" % name)
            continue
        modules.append((n, name, kind, CLASSES[kind](reg.uart, address=0x20 + n,
                                                     location=location(name))))
    return info, notes, modules


def parse_channels(text):
    """'1,3,5-8' -> [1, 3, 5, 6, 7, 8]; for argparse type=."""
    channels = []
    try:
        for part in text.split(","):
            first, _, last = part.partition("-")
            channels += range(int(first), int(last or first) + 1)
    except ValueError:
        raise argparse.ArgumentTypeError("bad channel list %r (e.g. 1,3,5-8)" % text)
    return sorted(set(channels))


# x12 status summary byte 6 (Tables 12/34; CERN-B Tables 24/39). Bit 0
# (init complete) clears on read and is not an alarm, so it is not listed.
SUMMARY_BITS = {
    "Tx": {7: "Tx LOS", 5: "Tx fault", 1: "temp/Vcc alarm"},
    "Rx": {6: "Rx LOS", 5: "fault", 2: "Rx low power", 1: "temp/Vcc alarm"},
}
# x12 bytes 17/18 (Tables 15/16).
X12_TEMP_BITS = {7: "temp high alarm", 6: "temp low alarm"}
X12_VCC_BITS = {7: "Vcc3.3 high alarm", 6: "Vcc3.3 low alarm"}
# x4 bytes 6/7 (Tables 32/33). Byte 6 bit 0 is the init-complete flag.
X4_TEMP_BITS = {7: "temp high alarm", 6: "temp low alarm",
                5: "temp high warning", 4: "temp low warning"}
X4_VCC_BITS = {7: "Vcc3.3 high alarm", 6: "Vcc3.3 low alarm",
               5: "Vcc3.3 high warning", 4: "Vcc3.3 low warning",
               3: "Vcc1.8 high alarm", 2: "Vcc1.8 low alarm",
               1: "Vcc1.8 high warning", 0: "Vcc1.8 low warning"}


def bit_names(value, table):
    return [table[b] for b in sorted(table, reverse=True) if value & (1 << b)]


def x12_channels(ff, reg):
    """Decode a 2-byte x12 per-channel latch: byte n low nibble = ch 9-12,
    byte n+1 = ch 1-8 (e.g. x12 Rev 4 Table 13). Returns 1-based channels."""
    hi, lo = ff.read_reg(reg, size=2)
    mask = ((hi & 0x0F) << 8) | lo
    return [ch + 1 for ch in range(12) if mask & (1 << ch)]


def x12_rx_power(ff):
    """Decode x12 Rx bytes 14-16 (Table 36): 2 bits per channel, byte 16
    bits 1-0 = ch 1 up to byte 14 bits 7-6 = ch 12; 01 = too low, 10 = too
    high. Returns (low, high) lists of 1-based channels."""
    raw = ff.read_reg(FireflyRx.Reg.RX_POWER_ALARM, size=3)
    value = int.from_bytes(raw, "big")
    low, high = [], []
    for ch in range(12):
        code = (value >> (2 * ch)) & 0x3
        if code & 0x1:
            low.append(ch + 1)
        if code & 0x2:
            high.append(ch + 1)
    return low, high


def x4_channels(value, shift):
    return [ch + 1 for ch in range(4) if value & (1 << (ch + shift))]


def chans(label, channels):
    return "%s ch %s" % (label, ",".join(str(c) for c in channels))


def family(part):
    """Same rule as the firmware's ff_map_25gb_parts()."""
    if "CERNB" in part or "CRRNB" in part:
        return "cernb"
    if "14" in part:
        return "14g"
    return "25g"


def read_x12(ff, kind, part):
    fam = family(part)
    problems = []
    status = ff.read_reg(FireflyReg.STATUS)[0]
    if status & 0x01:
        problems.append("data not ready")
    summary = ff.status_summary
    problems += bit_names(summary, SUMMARY_BITS[kind])
    # 14G map not checked against its manual; the firmware reads no latches
    # on it either, so stop at the summary.
    if fam != "14g":
        problems += bit_names(ff.temperature_alarm, X12_TEMP_BITS)
        problems += bit_names(ff.supply_voltage_alarm, X12_VCC_BITS)
        if kind == "Rx":
            los = x12_channels(ff, FireflyRx.Reg.RX_LOS_LATCH)
            if los:
                problems.append(chans("LOS", los))
            if fam == "25g":  # CERN-B Rx bytes 9-16 are reserved (Table 37)
                low, high = x12_rx_power(ff)
                if low:
                    problems.append(chans("Rx power low", low))
                if high:
                    problems.append(chans("Rx power high", high))
        else:
            if fam == "25g":  # CERN-B Tx has no LOS bytes (Table 22)
                los = x12_channels(ff, FireflyTx.Reg.TX_LOS_LATCH)
                if los:
                    problems.append(chans("LOS", los))
            fault = x12_channels(ff, FireflyTx.Reg.TX_FAULT_LATCH)
            if fault:
                problems.append(chans("fault", fault))
        if fam == "25g":  # CERN-B has no CDR
            lol = x12_channels(ff, FireflyTx.Reg.CDR_LOL_STATUS_BASE)
            if lol:
                problems.append(chans("CDR LOL", lol))
    return fam, "status=0x%02X summary=0x%02X" % (status, summary), problems


def read_x4(ff):
    problems = []
    status = ff.read_reg(Firefly4.Reg.STATUS)[0]
    if status & 0x01:
        problems.append("data not ready")
    los = ff.read_reg(Firefly4.Reg.LOS_ALARMS)[0]
    fault = ff.read_reg(Firefly4.Reg.TX_FAULT_LATCH)[0]
    lol = ff.read_reg(Firefly4.Reg.CDR_LOL_ALARMS)[0]
    for label, value, shift in (("Tx LOS", los, 4), ("Rx LOS", los, 0),
                                ("Tx fault", fault, 0),
                                ("Tx CDR LOL", lol, 4), ("Rx CDR LOL", lol, 0)):
        ch = x4_channels(value, shift)
        if ch:
            problems.append(chans(label, ch))
    problems += bit_names(ff.temperature_alarm, X4_TEMP_BITS)
    problems += bit_names(ff.supply_voltage_alarm, X4_VCC_BITS)
    return "x4", "status=0x%02X" % status, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(parser, "Firefly location", LOCATIONS)
    add_out_dir_arg(parser, "cm<boardid>_ff_status.txt")
    add_force_arg(parser)
    add_common_args(parser)
    args = parser.parse_args()
    wanted = selected_devices(parser, args, LOCATIONS)
    if args.all:
        wanted = None  # this board revision's slots, not every revision's

    reg = make_registry(args)
    power_text, power_state = power_info(reg)
    if not power_is_on(power_state, args.force):
        return 1
    info = reg.mcu.system_info
    if info.ff_present_mask is None:
        print("MCU map minor %d has no Firefly masks (needs >= 1)" % info.map_minor)
        return 1
    slots = SLOTS.get(info.hardware_revision)
    if slots is None:
        print("no Firefly slot table for board revision %d" % info.hardware_revision)
        return 1

    present, user = info.ff_present_mask, info.ff_user_mask
    populated = [n for n in range(len(slots)) if present & (1 << n)]
    lines = [
        "Firefly status  %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "board %d  rev %d  fw %s  cm_interface %s" % (
            info.board_id, info.hardware_revision, info.git_version, __version__),
        "power %s" % power_text,
        "fpga done %s" % fpga_done_info(reg),
        "present mask 0x%05X  user mask 0x%05X  populated %d of %d"
        % (present, user, len(populated), len(slots)),
    ]
    notes, to_read = select_slots(slots, populated, wanted, info.hardware_revision)
    lines += notes
    lines.append("%2s  %-8s %-6s %-16s %-10s %5s %6s  %s"
                 % ("#", "slot", "type", "part", "serial", "T(C)", "Vcc(V)", "status"))

    failed = False
    for n in to_read:
        name, kind = slots[n]
        if not user & (1 << n):
            lines.append("%2d  %-8s present but not in user mask; MCU will not read it"
                         % (n, name))
            continue
        ff = CLASSES[kind](reg.uart, address=0x20 + n, location=name)
        try:
            part = ff.part_id
            serial = ff.serial_number or "-"
            temp = ff.temperature
            vcc = ff.supply_voltage
            if kind == "4":
                fam, raw, problems = read_x4(ff)
            else:
                fam, raw, problems = read_x12(ff, kind, part)
        except Exception as exc:  # keep going so one bad module doesn't hide the rest
            failed = True
            lines.append("%2d  %-8s READ FAILED: %s" % (n, name, exc))
            continue
        lines.append("%2d  %-8s %-6s %-16s %-10s %5d %6.3f  %s  %s"
                     % (n, name, fam, part, serial, temp, vcc,
                        "ALARM" if problems else "OK", raw))
        for p in problems:
            lines.append("      %s" % p)
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        write_lines(out_path(args.out_dir, info.board_id, "ff_status"), lines, failed)
    else:
        print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
