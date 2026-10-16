#!/usr/bin/env python3
"""Dump the command-module MCU's ProgCom register pages (``MC 0``).

READ ONLY: this runs the same page reads as ``exercise_mcu.py`` with no
arguments (System, Power, Alarm, Config, Runtime, ADC, and the deferred
persistent-log pages as a best effort) and has no write or Control-page
path. Use ``exercise_mcu.py`` for those.

Uptime, RTC, heap/stack, the Power generation counter and the ADC values
change on every read; exclude them when diffing two dumps.

Usage:
    mcu_dump.py
    mcu_dump.py --out-dir DIR
"""
import argparse
import contextlib
import io
import os
import sys
import time

# Permit direct execution from this package directory (as exercise_mcu.py does).
if not __package__:
    _package_dir = os.path.dirname(os.path.realpath(__file__))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.exercise_mcu import (
    exercise_adc,
    exercise_alarm,
    exercise_config,
    exercise_persistent_log,
    exercise_power,
    exercise_runtime,
    exercise_system,
)
from cm_interface.lga80d_dump import board_info
from cm_interface.registry import Registry

SECTIONS = (
    exercise_system,
    exercise_power,
    exercise_alarm,
    exercise_config,
    exercise_runtime,
    exercise_adc,
    exercise_persistent_log,
)


class _CaptureBuffer(io.StringIO):
    """StringIO that reports the real stdout's encoding, so the warning
    marker exercise_mcu picks for the capture also prints to that stdout."""

    def __init__(self, encoding):
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self):
        return self._encoding


def dump_mcu(reg, args, board_lines, board_id):
    """Dump every page. Prints to stdout; also writes a file if --out-dir.
    Returns True if any section failed."""
    header = [
        "MCU register dump  %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "  uart:      %s" % args.dev_path,
    ] + board_lines
    mcu = reg.get_mcu()
    buf = _CaptureBuffer(getattr(sys.stdout, "encoding", None))
    failed = False
    with contextlib.redirect_stdout(buf):
        for section in SECTIONS:
            failed = section(mcu) or failed
    text = "\n".join(header) + "\n" + buf.getvalue()
    print(text, end="")
    if args.out_dir:
        path = os.path.join(args.out_dir, "cm%s_mcu.txt" % (
            "unknown" if board_id is None else board_id))
        # utf-8 explicitly: the warning marker may be non-ASCII, and Python
        # 3.6 under a C locale would otherwise default to ASCII.
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote %s%s" % (path, "  (read failures)" if failed else ""))
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dev-path", default="/dev/ttyUL4",
                        help="UART device (default: %(default)s)")
    parser.add_argument("--out-dir", metavar="DIR",
                        help="also write the dump to DIR, named cm<boardid>_mcu.txt "
                             "(an existing file is overwritten; board id is "
                             "'unknown' if the MCU can't report it)")
    args = parser.parse_args()

    reg = Registry(dev_path=args.dev_path)
    board_lines, board_id = board_info(reg)
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
    return 1 if dump_mcu(reg, args, board_lines, board_id) else 0


if __name__ == "__main__":
    sys.exit(main())
