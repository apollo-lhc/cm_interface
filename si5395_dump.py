#!/usr/bin/env python3
"""Summary dump of one Si5395 clock synthesizer.

READ ONLY: every access is a ProgCom "r CL ..." read; nothing is written and
sticky flags are not cleared. Field layout follows the Si5395/94/92 Reference
Manual Rev 1.3, chapter 16.

DEVICE_READY (0x00FE) is read first. Until it reads 0x0F the reference
manual permits no other register access, so a clock that is not ready is
reported and skipped.

Output, per clock:
  status    live status, sticky flags, monitor enables, NVM bank, global
            output enables and input/reference selection; one line per
            register, NAME=value(decoded) per bit field.
  outputs   one row per output driver (0x0103-0x013D) with its R divider
            (0x0247-0x026A). R = (R_REG+1)*2, or 2 when RDIV_FORCE2 is set.
  dividers  P0-P3, MXAXB, M (feedback ratio = 5 * M_NUM/M_DEN, /5 prescaler)
            and N0-N4 with the per-N bits from page A. Fout = Fvco / (N * R);
            N_ADD_0P5 is shown but not applied, the manual does not define it.

CBPro-computed tuning (LOS/OOF/LOL thresholds and windows, holdover,
fastlock, loop bandwidth codes, phase adjust) is not read.

Usage:
    si5395_dump.py R0A
    si5395_dump.py --all --out-dir DIR
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
from cm_interface.device.si5395 import Si5395Reg
from cm_interface.lga80d_dump import board_info
from cm_interface.registry import Registry

DEVICE_READY_VALUE = 0x0F
MAX_READ_BYTES = 4

# ---------------------------------------------------------------- decoders

def _letter(v):
    return chr(ord("A") + v) if 0 <= v < 26 else "?"


def _inputs(v):
    names = ["IN%d" % i for i in range(4) if v & (1 << i)]
    return ",".join(names) if names else "none"


def _enum(*names):
    return lambda v: names[v] if v < len(names) and names[v] else "rsvd"


def _ascii(raw):
    return repr(bytes(raw).rstrip(b"\x00 ").decode("ascii", "replace"))


PRIORITY = _enum("none", "1", "2", "3", "4")
IN_NUM = _enum("IN0", "IN1", "IN2", "IN3")

# field name -> decoder(value) returning a short string shown in parentheses.
FIELD_DECODE = {
    "LOS": _inputs, "OOF": _inputs, "LOS_FLG": _inputs, "OOF_FLG": _inputs,
    "LOS_EN": _inputs, "OOF_EN": _inputs, "FAST_OOF_EN": _inputs,
    "IN_EN": _inputs, "IN_PULSED_CMOS_EN": _inputs,
    "ACTIVE_NVM_BANK": lambda v: {0x03: "no user burn", 0x0F: "1 bank burned",
                                  0x3F: "2 banks burned, none left"}.get(v, "undocumented"),
    "DEVICE_READY": lambda v: "ready" if v == DEVICE_READY_VALUE else "NOT ready",
    "OUTALL_DISABLE_LOW": _enum("all outputs disabled", "pass OEs"),
    "OUT_PDN_ALL": _enum("no effect", "all drivers powered down"),
    "PXAXB": lambda v: "/%d" % (1 << v),
    "XAXB_EXTCLK_EN": _enum("crystal", "external clock"),
    "ZDM_IN_SEL": _enum("IN0", "IN1", "IN2"),
    "IN_ACTV": IN_NUM,
    "IN_SEL_REGCTRL": _enum("pin", "register"),
    "IN_SEL": IN_NUM,
    "CLK_SWTCH_MODE": _enum("manual", "auto non-revertive", "auto revertive"),
    "HSW_EN": _enum("glitchless", "hitless"),
    "IN0_PRIORITY": PRIORITY, "IN1_PRIORITY": PRIORITY,
    "IN2_PRIORITY": PRIORITY, "IN3_PRIORITY": PRIORITY,
    "HOLD_EN": _enum("off", "on"),
}

# ---------------------------------------------------------------- register map
# Summary registers: (addr, "FIELD:hi:lo FIELD:bit ..."). The output and
# divider tables below cover pages 1-3 and A separately. Everything else in
# the manual (LOS/OOF/LOL thresholds and windows, holdover tuning, fastlock,
# phase adjust) is CBPro-computed and deliberately left out.

REGS = [
    # live status and sticky flags
    (0x000C, "SYSINCAL:0 LOSXAXB:1 XAXB_ERR:3 SMBUS_TIMEOUT:5"),
    (0x000D, "LOS:3:0 OOF:7:4"),
    (0x000E, "LOL:1 HOLD:5"),
    (0x000F, "CAL_PLL:5"),
    (0x0011, "SYSINCAL_FLG:0 LOSXAXB_FLG:1 XAXB_ERR_FLG:3 SMBUS_TIMEOUT_FLG:5"),
    (0x0012, "LOS_FLG:3:0 OOF_FLG:7:4"),
    (0x0013, "LOL_FLG:1 HOLD_FLG:5"),
    (0x0014, "CAL_FLG_PLL:5"),
    # monitors enabled
    (0x002C, "LOS_EN:3:0 LOSXAXB_DIS:4"),
    (0x003F, "OOF_EN:3:0 FAST_OOF_EN:7:4"),
    (0x009A, "LOL_SLOW_EN_PLL:1"),
    # NVM, ready
    (0x00E2, "ACTIVE_NVM_BANK:7:0"),
    (0x00FE, "DEVICE_READY:7:0"),
    # global output control
    (0x0102, "OUTALL_DISABLE_LOW:0"),
    (0x0145, "OUT_PDN_ALL:0"),
    # reference and input selection
    (0x0206, "PXAXB:1:0"),
    (0x090E, "XAXB_EXTCLK_EN:0"),
    (0x0949, "IN_EN:3:0 IN_PULSED_CMOS_EN:7:4"),
    (0x0487, "ZDM_EN:0 ZDM_IN_SEL:2:1"),
    (0x0507, "IN_ACTV:7:6"),
    (0x052A, "IN_SEL_REGCTRL:0 IN_SEL:2:1"),
    (0x0536, "CLK_SWTCH_MODE:1:0 HSW_EN:2"),
    (0x0538, "IN0_PRIORITY:2:0 IN1_PRIORITY:6:4"),
    (0x0539, "IN2_PRIORITY:2:0 IN3_PRIORITY:6:4"),
    (0x052C, "HOLD_EN:0"),
    (0x0535, "FORCE_HOLD:0"),
]

IDENT = (0x0002, 4)   # PN_BASE (2 bytes), GRADE, DEVICE_REV
DESIGN_ID = (0x026B, 8)
OPN_ID = (0x0278, 5)

# Output drivers: name -> (first of 4 config regs, R divider register).
OUTPUTS = [
    ("OUT0A", 0x0103, 0x0247), ("OUT0", 0x0108, 0x024A), ("OUT1", 0x010D, 0x024D),
    ("OUT2", 0x0112, 0x0250), ("OUT3", 0x0117, 0x0253), ("OUT4", 0x011C, 0x0256),
    ("OUT5", 0x0121, 0x0259), ("OUT6", 0x0126, 0x025C), ("OUT7", 0x012B, 0x025F),
    ("OUT8", 0x0130, 0x0262), ("OUT9", 0x0135, 0x0265), ("OUT9A", 0x013A, 0x0268),
]
OUT_FORMAT = {1: "diff", 2: "diff-LP", 4: "LVCMOS"}
OUT_VDD = {0: "3.3V", 1: "1.8V", 2: "2.5V"}

# Dividers: (name, num addr, num bytes, den addr, frac/mode register or None)
P_DIVS = [("P%d" % i, 0x0208 + 10 * i, 6, 0x020E + 10 * i, 0x0231 + i) for i in range(4)]
MXAXB = ("MXAXB", 0x0235, 6, 0x023B, None)
M_DIV = ("M", 0x0515, 7, 0x051C, 0x0521)
# N0-N4: numerator, denominator, frequency step word, HIGH_FREQ register.
N_DIVS = [("N%d" % i, 0x0302 + 11 * i, 0x0308 + 11 * i, 0x033B + 6 * i, 0x0A14 + 6 * i)
          for i in range(5)]
N_BITS = (("PIBYP", 0x0A04), ("PDNB", 0x0A05), ("TO_OUT", 0x0A03), ("ADD0P5", 0x0A02),
          ("FSTEP_MSK", 0x0339), ("CLK_DIS", 0x0B4A))


def _parse_fields(spec):
    fields = []
    for item in spec.split():
        parts = item.split(":")
        hi = int(parts[1])
        lo = int(parts[2]) if len(parts) > 2 else hi
        fields.append((parts[0], hi, lo))
    return fields


def addresses():
    """Every register address the dump reads (sorted)."""
    addrs = set()
    for addr, _ in REGS:
        addrs.add(addr)
    for addr, nbytes in (IDENT, DESIGN_ID, OPN_ID):
        addrs.update(range(addr, addr + nbytes))
    for _, cfg, rdiv in OUTPUTS:
        addrs.update(range(cfg, cfg + 4))
        addrs.update(range(rdiv, rdiv + 3))
    for _, num, nnum, den, frac in P_DIVS + [MXAXB, M_DIV]:
        addrs.update(range(num, num + nnum))
        addrs.update(range(den, den + 4))
        if frac is not None:
            addrs.add(frac)
    for _, num, den, step, high in N_DIVS:
        addrs.update(range(num, num + 6))
        addrs.update(range(den, den + 4))
        addrs.update(range(step, step + 6))
        addrs.add(high)
    addrs.update(addr for _, addr in N_BITS)
    return sorted(addrs)


def read_all(clock, addrs):
    """Read addrs in runs of up to 4 contiguous bytes within one page.
    Returns ({addr: byte}, [(addr, error)]); a failed run is recorded, not raised."""
    values, errors = {}, []
    i = 0
    while i < len(addrs):
        start = addrs[i]
        n = 1
        while (i + n < len(addrs) and n < MAX_READ_BYTES and addrs[i + n] == start + n
               and (start + n) >> 8 == start >> 8):
            n += 1
        try:
            data = clock.read_reg(start, size=n)
            for k in range(n):
                values[start + k] = data[k]
        except Exception as exc:  # keep going; one bad read shouldn't hide the rest
            errors.append((start, exc))
        i += n
    return values, errors


class Values(object):
    """Byte cache with little-endian multi-byte access; None if any byte is missing."""

    def __init__(self, values):
        self.values = values

    def u8(self, addr):
        return self.values.get(addr)

    def word(self, addr, nbytes, bits=None):
        raw = [self.values.get(a) for a in range(addr, addr + nbytes)]
        if None in raw:
            return None
        value = int.from_bytes(bytes(raw), "little")
        return value & ((1 << bits) - 1) if bits else value

    def raw(self, addr, nbytes):
        raw = [self.values.get(a) for a in range(addr, addr + nbytes)]
        return None if None in raw else bytes(raw)


def _fmt_field(name, value):
    decode = FIELD_DECODE.get(name)
    return "%s=%d%s" % (name, value, "(%s)" % decode(value) if decode else "")


def _ratio(num, den):
    if num is None or den is None:
        return "?"
    if den == 0:
        return "den=0"
    return "%.9f" % (num / den)


def _bits(v, n=5):
    """Per-divider bitmask as a string, divider 0 first."""
    return "?" * n if v is None else "".join("1" if v & (1 << i) else "0" for i in range(n))


def identity_lines(vals):
    pn = vals.word(0x0002, 2)
    grade, rev = vals.u8(0x0004), vals.u8(0x0005)
    design = vals.raw(*DESIGN_ID)
    opn = vals.raw(*OPN_ID)
    part = "?" if None in (pn, grade, rev) else "Si%04X%s-%s" % (pn, _letter(grade), _letter(rev))
    return ["  part:      %s  OPN_ID %s  DESIGN_ID %s" % (
        part, "?" if opn is None else _ascii(opn), "?" if design is None else _ascii(design))]


def output_lines(vals):
    lines = ["", "  -- outputs (0x0103-0x013D, R dividers 0x0247-0x026A) --",
             "  %-6s %3s %2s %-7s %4s %3s %4s %4s %3s %3s %-4s %4s %3s %8s %12s" % (
                 "OUTPUT", "PDN", "OE", "FORMAT", "SYNC", "DIS", "CDRV", "AMPL", "CM",
                 "MUX", "VDD", "VSEL", "INV", "R", "N*R")]
    n_ratio = {}
    for name, num, den, _, _ in N_DIVS:
        n, d = vals.word(num, 6, 44), vals.word(den, 4)
        n_ratio[name] = None if n is None or d is None or d == 0 else n / d
    for name, cfg, rdiv in OUTPUTS:
        r0, r1, r2, r3 = (vals.u8(cfg + k) for k in range(4))
        rreg = vals.word(rdiv, 3)
        if None in (r0, r1, r2, r3, rreg):
            lines.append("  %-6s READ FAILED" % name)
            continue
        if r0 & 0x04:
            r = 2
            rtxt = "2(F2)"
        elif rreg == 0:
            r = None
            rtxt = "REG=0?"
        else:
            r = (rreg + 1) * 2
            rtxt = str(r)
        mux = r3 & 0x07
        src = "N%d" % mux if mux <= 4 else "rsvd"
        nr = n_ratio.get(src)
        lines.append("  %-6s %3d %2d %-7s %4d %3s %4d %4d %3d %3s %-4s %4d %3d %8s %12s" % (
            name, r0 & 1, (r0 >> 1) & 1, OUT_FORMAT.get(r1 & 7, "rsvd%d" % (r1 & 7)),
            (r1 >> 3) & 1, "LH??"[(r1 >> 4) & 3], (r1 >> 6) & 3, (r2 >> 4) & 7, r2 & 0xF,
            src, OUT_VDD.get((r3 >> 4) & 3, "rsvd"), (r3 >> 3) & 1, (r3 >> 6) & 3, rtxt,
            "?" if nr is None or r is None else "%.6f" % (nr * r)))
    return lines


def divider_lines(vals):
    lines = ["", "  -- dividers P0-P3, MXAXB, M (0x0208-0x023E, 0x0515-0x0521) --",
             "  %-6s %18s %12s %18s  %s" % ("DIV", "NUM", "DEN", "NUM/DEN", "FRAC")]
    for name, num, nnum, den, frac in P_DIVS + [MXAXB, M_DIV]:
        n, d = vals.word(num, nnum), vals.word(den, 4)
        extra = ""
        if frac is not None:
            f = vals.u8(frac)
            extra = "?" if f is None else "FRAC_EN=%d FRACN_MODE=0x%X" % ((f >> 4) & 1, f & 0xF)
        if name == "M" and n is not None and d:
            extra += "  feedback 5*M = %.9f" % (5 * n / d)
        lines.append("  %-6s %18s %12s %18s  %s" % (
            name, "?" if n is None else n, "?" if d is None else d, _ratio(n, d), extra))

    bits = dict((label, vals.u8(addr)) for label, addr in N_BITS)
    lines += ["", "  -- N dividers (0x0302-0x0358, page A bits 0x0A02-0x0A2C, 0x0B4A) --",
              "  %-3s %15s %12s %16s %5s %4s %6s %6s %5s %5s %7s %15s" % (
                  "N", "NUM", "DEN", "NUM/DEN", "INT", "PDNB", "TO_OUT", "ADD0P5",
                  "HIGHF", "FSMSK", "CLK_DIS", "FSTEPW")]
    for i, (name, num, den, step, high) in enumerate(N_DIVS):
        n, d = vals.word(num, 6, 44), vals.word(den, 4)
        h = vals.u8(high)

        def bit(label):
            v = bits[label]
            return "?" if v is None else (v >> i) & 1

        fstep = vals.word(step, 6, 44)
        lines.append("  %-3s %15s %12s %16s %5s %4s %6s %6s %5s %5s %7s %15s" % (
            name, "?" if n is None else n, "?" if d is None else d, _ratio(n, d),
            bit("PIBYP"), bit("PDNB"), bit("TO_OUT"), bit("ADD0P5"),
            "?" if h is None else (h >> 3) & 1, bit("FSTEP_MSK"), bit("CLK_DIS"),
            "?" if fstep is None else fstep))
    return lines


def field_lines(vals):
    lines = ["", "  -- status and configuration --", "  %-7s %-4s  %s" % ("ADDR", "RAW", "FIELDS")]
    for addr, spec in sorted(REGS):
        v = vals.u8(addr)
        if v is None:
            lines.append("  0x%04X  READ FAILED  %s" % (addr, spec))
            continue
        text = "  ".join(_fmt_field(name, (v >> lo) & ((1 << (hi - lo + 1)) - 1))
                         for name, hi, lo in _parse_fields(spec))
        lines.append("  0x%04X  0x%02X  %s" % (addr, v, text))
    return lines


def dump_clock(reg, name, args, board_lines, board_id, echo):
    """Dump one clock. Prints to stdout if echo; writes a file if --out-dir.
    Returns True if anything failed."""
    clock = reg.get_clock(name)
    lines = [
        "Si5395 register dump  %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "  clock:     %s (address 0x%02X)" % (name, CORE_CONFIG.clocks[name]),
        "  uart:      %s" % args.dev_path,
    ] + board_lines
    failed = False
    try:
        ready = clock.read_reg(Si5395Reg.DEVICE_READY)[0]
    except Exception as exc:
        ready = None
        lines.append("  DEVICE_READY READ FAILED: %s" % exc)
    if ready is not None and ready != DEVICE_READY_VALUE:
        lines.append("  NOT READY: DEVICE_READY 0x%02X (expect 0x%02X); no further reads"
                     % (ready, DEVICE_READY_VALUE))
    if ready != DEVICE_READY_VALUE:
        failed = True
        result = "READ FAILED" if ready is None else "NOT READY"
    else:
        values, errors = read_all(clock, addresses())
        vals = Values(values)
        lines += identity_lines(vals)
        for addr, exc in errors:
            lines.append("  READ FAILED at 0x%04X: %s" % (addr, exc))
        failed = bool(errors)
        result = "READ FAILED (%d)" % len(errors) if errors else "OK"
        lines += field_lines(vals) + output_lines(vals) + divider_lines(vals)
    result_line = "%-4s result: %s" % (name, result)
    lines.append(result_line)
    if echo:
        print("\n".join(lines))
    if args.out_dir:
        path = os.path.join(args.out_dir, "cm%s_%s.txt" % (
            "unknown" if board_id is None else board_id, name))
        with open(path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        print("wrote %s" % path)
        if not echo:
            print(result_line)
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("device", nargs="?", choices=list(CORE_CONFIG.clocks),
                        help="clock name")
    parser.add_argument("--all", action="store_true",
                        help="dump every clock (requires --out-dir; only the "
                             "'wrote' lines go to stdout)")
    parser.add_argument("--dev-path", default="/dev/ttyUL4",
                        help="UART device (default: %(default)s)")
    parser.add_argument("--out-dir", metavar="DIR",
                        help="also write one file per clock to DIR, named "
                             "cm<boardid>_<clock>.txt (existing files are overwritten; "
                             "board id is 'unknown' if the MCU can't report it)")
    args = parser.parse_args()
    if args.all == (args.device is not None):
        parser.error("give either a clock name or --all")
    if args.all and not args.out_dir:
        parser.error("--all requires --out-dir")

    reg = Registry(dev_path=args.dev_path)
    board_lines, board_id = board_info(reg)
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
    names = list(CORE_CONFIG.clocks) if args.all else [args.device]
    failed = False
    for name in names:
        failed = dump_clock(reg, name, args, board_lines, board_id,
                            echo=not args.all) or failed
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
