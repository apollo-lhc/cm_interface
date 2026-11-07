#!/usr/bin/env python3
"""Register dump of the populated Fireflies (all, or the locations named).

Modules are found the same way as cm-ff-status: the MCU's present and user
masks plus the board revision's slot table. A location selects both halves
of an x12 pair; each half is a separate device and is dumped separately.

Per module:
  raw       bytes 0x00-0xFF in hex: the lower page (0x00-0x7F) and upper
            page 00h (0x80-0xFF, vendor ID). Read in 4-byte transactions; a
            failed transaction shows as '??' and the rest is still read.
  decoded   the fields device/firefly.py knows, decoded from those same raw
            bytes (nothing is read twice): ID strings, temperature, Vcc,
            elapsed time, status/summary, latched alarms, and on 25G x12 and
            x4 the CDR, disable and mask registers the device classes define
            (x12 Rx has no temp/Vcc alarm mask there). 25G x12 Rx also shows the
            Rx output amplitude and pre-emphasis codes. CERN-B and 14G x12
            get the latches cm-ff-status reads and no control registers.
            x4 also shows the Rx power alarm latches (bytes 9-10) and Rx
            optical power (bytes 34-41, 0.1 uW, also in dBm) and Vcc1.8.
  page 01h  25G x12 Rx only: the per-channel RSSI (Rx optical power,
            Table 52, bytes 0xCE-0xE5, 0.1 uW, also in dBm), shown raw
            and decoded.

Side effects: reading the latched alarm bytes and the summary byte clears
them (summary bit 0, init complete, too). The MCU already polls these same
bytes (see cm-ff-status), so this adds no new effect. The upper-page read
makes the MCU write PAGE_SELECT (0x7F) = 00h, as its own ID reads do. The
x4 needs up to 100 ms after a page change (manual Table 25), which the MCU
does not wait, so a throwaway read selects the page and the tool sleeps
100 ms before reading it. For page 01h (25G x12 Rx) the same is done, and
afterwards page 00h is selected again the same way, so the module is left
on page 00h for the MCU. Upper pages 02h, 03h and 0Bh are not read.

Usage:
    cm-ff-dump --all
    cm-ff-dump F1_1 F2_6
    cm-ff-dump --all --out-dir DIR
"""
import argparse
import math
import os
import sys
import time

# Permit direct execution from a checkout (./tools/NAME.py) without the RPM.
if not __package__:
    _package_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.device.firefly import Firefly4, FireflyReg, FireflyRx, FireflyTx
from cm_interface.tools._common import (add_common_args, add_device_args, add_force_arg,
                                       add_out_dir_arg, board_info, make_registry, out_path,
                                       power_info, power_is_on, selected_devices, state_lines,
                                       write_lines)
from cm_interface.tools.ff_status import (LOCATIONS, SUMMARY_BITS, X12_TEMP_BITS, X12_VCC_BITS,
                                         X4_TEMP_BITS, X4_VCC_BITS, bit_names, family,
                                         find_modules)


DUMP_LENGTH = 0x100
UPPER_START = 0x80
CHUNK = 4
# x4 manual Table 25 note 14: up to 100 ms after PAGE_SELECT changes between
# 00h/01h (also 03h/0Bh) before the upper page is accessible. The MCU writes
# PAGE_SELECT and reads in one ProgCom transaction with no wait, so the first
# upper read after a page change can return stale data. The x12 datasheets
# give no such time; the wait is applied to every module anyway.
PAGE_SELECT_WAIT_S = 0.1


def select_page(ff, page):
    """Make the MCU write PAGE_SELECT = page with a throwaway 1-byte upper
    read, then wait PAGE_SELECT_WAIT_S. Returns the read's exception or None."""
    error = None
    try:
        ff.read_reg((page << 8) | UPPER_START)  # result discarded: may predate the change
    except Exception as exc:
        error = exc
    time.sleep(PAGE_SELECT_WAIT_S)
    return error


def read_raw(ff):
    """Return (list of DUMP_LENGTH ints or None per byte, first error or None).
    Each 4-byte transaction that fails leaves its bytes None.

    Before the upper page, one throwaway read selects page 00h, then
    PAGE_SELECT_WAIT_S passes. The later upper reads rewrite the same page
    value; the manual only times page *changes*, so they are assumed to need
    no further wait."""
    raw = [None] * DUMP_LENGTH
    error = None
    for addr in range(0, DUMP_LENGTH, CHUNK):
        if addr == UPPER_START:
            error = select_page(ff, 0) or error
        try:
            data = ff.read_reg(addr, size=CHUNK)
        except Exception as exc:  # keep going; one bad read shouldn't hide the rest
            error = error or exc
            continue
        raw[addr:addr + CHUNK] = list(data)
    return raw, error


RSSI_START, RSSI_LENGTH = FireflyRx.Reg.RX_OPTICAL_POWER & 0xFF, 24


def read_rx_rssi(ff):
    """25G x12 Rx upper page 01h RSSI bytes. Returns (DUMP_LENGTH list with
    only RSSI_START..+RSSI_LENGTH filled, first error or None). Selects page
    01h first and page 00h again afterwards, each with the wait."""
    page01 = [None] * DUMP_LENGTH
    error = select_page(ff, 1)
    for addr in range(RSSI_START, RSSI_START + RSSI_LENGTH, CHUNK):
        try:
            data = ff.read_reg(0x0100 | addr, size=CHUNK)
        except Exception as exc:
            error = error or exc
            continue
        page01[addr:addr + CHUNK] = list(data)
    return page01, select_page(ff, 0) or error


def hex_lines(raw, title="raw (0x00-0x7F lower page, 0x80-0xFF upper page 00h)",
              start=0, end=DUMP_LENGTH):
    lines = ["  " + title]
    for row in range(start & ~0xF, end, 16):
        cells = [("??" if raw[a] is None else "%02X" % raw[a])
                 if start <= a < end else "  " for a in range(row, row + 16)]
        lines.append("  %02X: %s  %s" % (row, " ".join(cells[:8]), " ".join(cells[8:])))
    return lines


class Missing(Exception):
    """A byte the decode needs was not read."""


def get(raw, addr, size=1):
    """Big-endian unsigned value of raw[addr:addr+size]."""
    data = raw[addr:addr + size]
    if None in data:
        raise Missing()
    return int.from_bytes(bytes(data), "big")


def get_ascii(raw, addr, length):
    data = raw[addr:addr + length]
    if None in data:
        raise Missing()
    return bytes(data).rstrip(b"\x00\xff ").decode("ascii", errors="replace")


def channel_text(channels):
    return "ch " + ",".join(str(c) for c in channels) if channels else "none"


def mask12(raw, addr):
    """12-channel 2-byte field: addr low nibble = ch 9-12, addr+1 = ch 1-8."""
    value = get(raw, addr, 2)
    mask = ((value >> 8) & 0x0F) << 8 | (value & 0xFF)
    return "0x%04X  %s" % (value, channel_text([ch + 1 for ch in range(12) if mask & (1 << ch)]))


def bits_text(value, table):
    return "0x%02X  %s" % (value, ", ".join(bit_names(value, table)) or "none")


def x4_halves(value):
    """x4 byte with Tx ch 1-4 in bits 4-7 and Rx ch 1-4 in bits 0-3."""
    tx = [ch for ch in range(1, 5) if value & (1 << (ch + 3))]
    rx = [ch for ch in range(1, 5) if value & (1 << (ch - 1))]
    return "0x%02X  Tx %s; Rx %s" % (value, channel_text(tx), channel_text(rx))


def x4_low(value):
    return "0x%02X  %s" % (value, channel_text([ch for ch in range(1, 5)
                                                if value & (1 << (ch - 1))]))


def nibbles(raw, addr, order, codes):
    """Packed nibble field, first byte's high nibble = order[0]."""
    data = get(raw, addr, len(order) // 2).to_bytes(len(order) // 2, "big")
    values = {}
    for i, ch in enumerate(order):
        values[ch] = (data[i // 2] >> (4 if i % 2 == 0 else 0)) & 0xF
    return "  ".join("%d:%X(%s)" % (ch, values[ch], codes.get(values[ch], "reserved"))
                     for ch in sorted(values))


def common_fields(ff, raw):
    """Fields every variant has, at the class's addresses."""
    fields = [("part", lambda: get_ascii(raw, ff.PART_ID_START, ff.PART_ID_LENGTH))]
    if ff.VENDOR_NAME_START is not None:
        fields.append(("vendor", lambda: get_ascii(raw, ff.VENDOR_NAME_START,
                                                   ff.VENDOR_NAME_LENGTH)))
    if ff.REVISION_NUMBER_START is not None:
        fields.append(("revision", lambda: get_ascii(raw, ff.REVISION_NUMBER_START,
                                                     ff.REVISION_NUMBER_LENGTH)))
    if ff.SERIAL_NUMBER_START is not None:
        fields.append(("serial", lambda: get_ascii(raw, ff.SERIAL_NUMBER_START,
                                                   ff.SERIAL_NUMBER_LENGTH)))
    if ff.EEPROM_REV_START is not None:
        fields.append(("eeprom rev", lambda: "0x%02X" % get(raw, ff.EEPROM_REV_START)))
    if ff.FIRMWARE_VER_START is not None:
        fields.append(("firmware", lambda: "%d.%d.%d" % tuple(
            get(raw, ff.FIRMWARE_VER_START + i) for i in range(3))))
    fields += [
        ("temperature", lambda: "%d C" % int.from_bytes(
            bytes([get(raw, FireflyReg.TEMP_MONITOR)]), "big", signed=True)),
        ("Vcc3.3", lambda: "%.4f V" % (get(raw, FireflyReg.VCC_MONITOR, 2) * 100e-6)),
        ("status", lambda: bits_text(get(raw, FireflyReg.STATUS),
                                     {1: "Int_L asserted", 0: "data not ready"})),
    ]
    return fields


def x12_fields(raw, kind, fam):
    reg = FireflyTx.Reg if kind == "Tx" else FireflyRx.Reg
    summary_bits = dict(SUMMARY_BITS[kind])
    summary_bits[0] = "init complete"
    fields = [
        ("elapsed", lambda: "%d h" % (get(raw, reg.ELAPSED_TIME, 2) * 2)),
        ("summary", lambda: bits_text(get(raw, reg.STATUS), summary_bits)),
    ]
    # 14G map not checked against its manual (as in cm-ff-status).
    if fam == "14g":
        return fields
    fields += [
        ("temp alarm", lambda: bits_text(get(raw, reg.TEMP_ALARM), X12_TEMP_BITS)),
        ("Vcc alarm", lambda: bits_text(get(raw, reg.VCC_ALARM), X12_VCC_BITS)),
    ]
    if kind == "Rx":
        fields.append(("LOS latch", lambda: mask12(raw, reg.RX_LOS_LATCH)))
    elif fam == "25g":  # CERN-B Tx has no LOS bytes
        fields.append(("LOS latch", lambda: mask12(raw, reg.TX_LOS_LATCH)))
    if kind == "Tx":
        fields.append(("fault latch", lambda: mask12(raw, reg.TX_FAULT_LATCH)))
    if fam != "25g":
        return fields
    if kind == "Rx":
        fields.append(("Rx power alarm", lambda: _rx_power(raw)))
    fields += [
        ("CDR LOL", lambda: mask12(raw, reg.CDR_LOL_STATUS_BASE)),
        ("CDR enable", lambda: mask12(raw, reg.CDR_ENABLE_BASE)),
        ("CDR LOL mask", lambda: mask12(raw, reg.CDR_LOL_MASK_BASE)),
    ]
    if kind == "Tx":
        fields += [
            ("Tx disable", lambda: mask12(raw, reg.TX_DISABLE)),
            ("Tx polarity inv", lambda: mask12(raw, reg.TX_POLARITY)),
            ("Tx fault mask", lambda: mask12(raw, reg.TX_FAULT_MASK)),
            ("temp alarm mask", lambda: bits_text(get(raw, reg.TEMP_ALARM_MASK), X12_TEMP_BITS)),
            ("Vcc alarm mask", lambda: bits_text(get(raw, reg.VCC_ALARM_MASK), X12_VCC_BITS)),
        ]
    else:
        fields += [
            ("Rx disable", lambda: mask12(raw, reg.RX_DISABLE)),
            ("Rx output disable", lambda: mask12(raw, reg.RX_OUTPUT_DISABLE)),
            ("Rx LOS mask", lambda: mask12(raw, reg.RX_LOS_MASK)),
            ("Rx amplitude", lambda: nibbles(raw, reg.RX_OUTPUT_AMP, FireflyRx.NIBBLE_ORDER,
                                             FireflyRx.AMPLITUDE_CODES)),
            ("Rx pre-emphasis", lambda: nibbles(raw, reg.RX_DEEMPHASIS, FireflyRx.NIBBLE_ORDER,
                                                FireflyRx.PREEMPHASIS_CODES)),
        ]
    return fields


def _rx_power(raw):
    """x12 Rx bytes 14-16: 2 bits per channel, byte 16 bits 1-0 = ch 1;
    01 = too low, 10 = too high (as cm-ff-status)."""
    value = get(raw, FireflyRx.Reg.RX_POWER_ALARM, 3)
    low = [ch + 1 for ch in range(12) if (value >> (2 * ch)) & 0x1]
    high = [ch + 1 for ch in range(12) if (value >> (2 * ch)) & 0x2]
    return "0x%06X  low %s; high %s" % (value, channel_text(low), channel_text(high))


X4_RX_POWER_BITS = ("high alarm", "low alarm", "high warning", "low warning")


def _x4_rx_power_alarms(raw):
    """x4 bytes 9-10 (Table 34): byte 9 bits 7-4 = Rx1, 3-0 = Rx2; byte 10
    the same for Rx3/Rx4. Within a channel's nibble, bit 3 down to bit 0 =
    high alarm, low alarm, high warning, low warning."""
    value = get(raw, Firefly4.Reg.RX_POWER_ALARMS, 2)
    found = []
    for ch in range(1, 5):
        nibble = (value >> (16 - 4 * ch)) & 0xF
        found += ["ch %d %s" % (ch, X4_RX_POWER_BITS[i])
                  for i in range(4) if nibble & (0x8 >> i)]
    return "0x%04X  %s" % (value, ", ".join(found) or "none")


def optical_power_text(counts):
    """0.1 uW counts -> 'uW (dBm)'; 0 has no dBm value."""
    uw = counts * 0.1
    if counts == 0:
        return "0.0 uW"
    return "%.1f uW (%.2f dBm)" % (uw, 10 * math.log10(uw / 1000.0))


def _x4_optical_power(raw, ch):
    return optical_power_text(get(raw, Firefly4.Reg.RX_OPTICAL_POWER + 2 * (ch - 1), 2))


def x4_fields(raw):
    reg = Firefly4.Reg
    return [
        ("elapsed", lambda: "%d h" % (get(raw, reg.ELAPSED_TIME, 2) * 2)),
        ("Vcc1.8", lambda: "%.4f V" % (get(raw, reg.VCC18_MONITOR, 2) * 100e-6)),
        ("Rx power alarm", lambda: _x4_rx_power_alarms(raw)),
    ] + [
        ("Rx%d optical power" % ch, lambda ch=ch: _x4_optical_power(raw, ch))
        for ch in range(1, 5)
    ] + [
        ("LOS latch", lambda: x4_halves(get(raw, reg.LOS_ALARMS))),
        ("fault latch", lambda: x4_low(get(raw, reg.TX_FAULT_LATCH))),
        ("CDR LOL", lambda: x4_halves(get(raw, reg.CDR_LOL_ALARMS))),
        ("temp alarm", lambda: bits_text(get(raw, reg.TEMP_ALARM), X4_TEMP_BITS)),
        ("Vcc alarm", lambda: bits_text(get(raw, reg.VCC_ALARM), X4_VCC_BITS)),
        ("Tx disable", lambda: x4_low(get(raw, reg.TX_DISABLE))),
        ("CDR enable", lambda: x4_halves(get(raw, reg.CDR_ENABLE))),
        ("CDR rate select", lambda: "0x%02X" % get(raw, reg.CDR_RATE_SELECT)),
        ("LOS mask", lambda: x4_halves(get(raw, reg.LOS_MASK))),
        ("Tx fault mask", lambda: x4_low(get(raw, reg.TX_FAULT_MASK))),
        ("CDR LOL mask", lambda: x4_halves(get(raw, reg.CDR_LOL_MASK))),
        ("temp alarm mask", lambda: bits_text(get(raw, reg.TEMP_MASK), X4_TEMP_BITS)),
        ("Vcc alarm mask", lambda: bits_text(get(raw, reg.VCC_MASK), X4_VCC_BITS)),
    ]


def rssi_fields(page01):
    """Descending channel order: RSSI_START = ch 12 .. RSSI_START+22 = ch 1."""
    return [("Rx%d optical power" % ch,
             lambda ch=ch: optical_power_text(get(page01, RSSI_START + 2 * (12 - ch), 2)))
            for ch in range(1, 13)]


def wants_rssi(kind, fam):
    return kind == "Rx" and fam == "25g"


def module_family(ff, raw):
    try:
        return family(get_ascii(raw, ff.PART_ID_START, ff.PART_ID_LENGTH))
    except Missing:
        return None


def decoded_lines(ff, kind, raw, page01=None):
    """Return (variant text, decoded lines). Never raises: a field whose bytes
    were not read shows 'not read'."""
    fam = module_family(ff, raw)
    if kind == "4":
        variant, extra = "x4", x4_fields(raw)
    elif fam is None:
        variant, extra = "x12 %s, family unknown (part not read)" % kind, []
    else:
        variant, extra = "x12 %s, %s" % (kind, fam), x12_fields(raw, kind, fam)
        if page01 is not None and wants_rssi(kind, fam):
            extra += rssi_fields(page01)
    lines = ["  decoded"]
    for label, fn in common_fields(ff, raw) + extra:
        try:
            text = fn()
        except Missing:
            text = "not read"
        lines.append("  %-18s %s" % (label, text))
    return variant, lines


def dump_module(n, name, kind, ff):
    """Return (lines, failed) for one module."""
    raw, error = read_raw(ff)
    page01 = None
    if wants_rssi(kind, module_family(ff, raw)):
        page01, rssi_error = read_rx_rssi(ff)
        error = error or rssi_error
    variant, decoded = decoded_lines(ff, kind, raw, page01)
    lines = ["", "== %s  (slot %d, %s) ==" % (name, n, variant)]
    if error is not None:
        lines.append("  READ FAILED (some bytes): %s" % error)
    lines += hex_lines(raw)
    if page01 is not None:
        lines += hex_lines(page01, "raw upper page 01h (RSSI bytes only)",
                           RSSI_START, RSSI_START + RSSI_LENGTH)
    return lines + [""] + decoded, error is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(parser, "Firefly location", LOCATIONS)
    add_out_dir_arg(parser, "cm<boardid>_ff_<slot>.txt, one file per module "
                            "(e.g. cm12_ff_F1_1_Tx.txt)")
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
    try:
        info, notes, modules = find_modules(reg, wanted)
    except ValueError as exc:
        print(exc)
        return 1
    board_lines, board_id = board_info(reg)
    header = [
        "Firefly register dump  %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "  uart:      %s" % args.dev_path,
    ] + board_lines + state_lines(reg, power_text) + [
        "  ff masks:  present 0x%05X  user 0x%05X" % (info.ff_present_mask, info.ff_user_mask),
    ] + ["  note:      %s" % note for note in notes]

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        for note in notes:
            print(note)
    else:
        print("\n".join(header))
    failed = False
    for n, name, kind, ff in modules:
        lines, module_failed = dump_module(n, name, kind, ff)
        failed = failed or module_failed
        if args.out_dir:
            write_lines(out_path(args.out_dir, board_id, "ff_%s" % name.replace(" ", "_")),
                        header + lines, module_failed)
        else:
            print("\n".join(lines))
    if not modules:
        print("no Firefly modules to dump")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
