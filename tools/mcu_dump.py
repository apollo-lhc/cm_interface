#!/usr/bin/env python3
"""Dump the command-module MCU's ProgCom register pages (``MC 0``).

READ ONLY: this runs the same page reads as ``cm-exercise-mcu`` with no
arguments (System, Power, Alarm, Config, Runtime, ADC, and the deferred
persistent-log pages as a best effort) and has no write or Control-page
path. Use ``cm-exercise-mcu`` for those.

Uptime, RTC, heap/stack, the Power generation counter and the ADC values
change on every read; exclude them when diffing two dumps.

Usage:
    cm-mcu-dump
    cm-mcu-dump --out-dir DIR
"""
import argparse
import contextlib
import io
import os
import sys
import time

# Permit direct execution from a checkout (./tools/NAME.py) without the RPM.
if not __package__:
    _package_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.tools.exercise_mcu import (
    exercise_adc,
    exercise_alarm,
    exercise_config,
    exercise_persistent_log,
    exercise_power,
    exercise_runtime,
    exercise_system,
)
from cm_interface.tools._common import (add_common_args, add_out_dir_arg, board_info,
                                       make_registry, out_path, write_lines)

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
    """Dump every page. Writes a file if --out-dir, else prints to stdout.
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
    if args.out_dir:
        # write_lines writes utf-8: the warning marker may be non-ASCII.
        write_lines(out_path(args.out_dir, board_id, "mcu"), text.rstrip("\n").split("\n"),
                    failed)
    else:
        print(text, end="")
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_out_dir_arg(parser, "the dump, cm<boardid>_mcu.txt,")
    add_common_args(parser)
    args = parser.parse_args()

    reg = make_registry(args)
    board_lines, board_id = board_info(reg)
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
    return 1 if dump_mcu(reg, args, board_lines, board_id) else 0


if __name__ == "__main__":
    sys.exit(main())
