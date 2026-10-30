#!/usr/bin/env python3
"""Dump the command-module MCU's ProgCom register pages (``MC 0``).

READ ONLY: reads System, Power, Alarm, Config, Runtime and ADC, and the
deferred persistent-log pages as a best effort. Each page is reported
independently, so one the firmware doesn't implement doesn't stop the rest.
Writes and Control-page commands are in ``cm-mcu-control``.

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

from cm_interface.device.mcu import describe_reset_cause
from cm_interface.errors import McuCapabilityUnavailable
from cm_interface.tools._common import (EXPECTED_HARDWARE_ERRORS, add_common_args,
                                       add_out_dir_arg, board_info, format_error, warn,
                                       flag_names, fpga_done_text, make_registry, out_path,
                                       state_lines, write_lines)

def _enum(value):
    """NAME (number): unlike repr(), the same on every Python version."""
    return f"{value.name} ({int(value)})"


def _flags(value, digits=2):
    """Set member names in bit order, then the raw value: 'A, B (0x03)' or
    'none (0x00)'. Bits with no member show as one extra hex term."""
    names = flag_names(value)
    unknown = int(value) & ~sum(int(m) for m in type(value))
    if unknown:
        names.append(f"0x{unknown:X}")
    return f"{', '.join(names) or 'none'} (0x{int(value):0{digits}X})"


def _run_section(title, fn):
    """Run one section; return True if it failed (an unadvertised page is not
    a failure)."""
    print(f"\n--- {title} " + "-" * max(0, 50 - len(title)))
    try:
        fn()
    except McuCapabilityUnavailable:
        print("  not advertised by firmware")
    except EXPECTED_HARDWARE_ERRORS as exc:
        warn(format_error(exc))
        return True
    return False


def dump_system(mcu):
    def go():
        info = mcu.system_info
        print(f"  map version:      {info.map_major}.{info.map_minor}")
        print(f"  hardware rev:     {info.hardware_revision}")
        print(f"  board id:         {info.board_id}")
        print(f"  uptime:           {info.uptime_seconds} s")
        print(f"  reset cause:      0x{info.reset_cause:08X}")
        for detail in describe_reset_cause(info.reset_cause):
            print(f"    - {detail}")
        print(f"  git version:      {info.git_version or '(empty)'}")
        print(f"  capabilities:     {_flags(info.capabilities, 8)}")
        print(f"  health:           {_flags(info.health)}")
        if info.ff_user_mask is None:
            for label in ("ff user mask", "ff present mask", "build type",
                          "build time"):
                print(f"  {label + ':':<17s} (requires map minor >= 1)")
        else:
            print(f"  ff user mask:     0x{info.ff_user_mask:08X}")
            print(f"  ff present mask:  0x{info.ff_present_mask:08X}")
            print(f"  build type:       {_enum(info.build_type)}")
            print(f"  build time:       {info.build_time or '(empty)'}")
    return _run_section("System (0x00)", go)


def dump_config(mcu):
    def go():
        cfg = mcu.read_alarm_config()
        print(f"  temp threshold FF:    {cfg.alarm_temp_ff} C")
        print(f"  temp threshold DCDC:  {cfg.alarm_temp_dcdc} C")
        print(f"  temp threshold TM4C:  {cfg.alarm_temp_tm4c} C")
        print(f"  temp threshold FPGA:  {cfg.alarm_temp_fpga} C")
        print(f"  voltage threshold:    {cfg.alarm_volt_threshold_percent:.2f} %")
    return _run_section("Config (0x05)", go)


def dump_runtime(mcu):
    def go():
        info = mcu.read_runtime()
        print(f"  heap free/min/total: {info.heap_free_bytes} / "
              f"{info.heap_min_ever_free_bytes} / {info.heap_total_bytes} bytes")
        print(f"  system stack:        {info.system_stack_untouched_words} of "
              f"{info.system_stack_total_words} words untouched")
        print(f"  zynqmon transmit:    {info.zynqmon_transmit_enabled}")
        print(f"  fpga done:           {fpga_done_text(info.fpga_done)}")
        print(f"  rtc:                 {info.rtc.isoformat()}")
    return _run_section("Runtime (0x06)", go)


def dump_adc(mcu):
    def go():
        snapshot = mcu.read_adc()
        for reading in snapshot.readings:
            flag = "" if reading.valid else "  (NaN / unpublished)"
            print(f"  {reading.index:2d} {reading.name:<16s} {reading.value:10.4f}{flag}")
    return _run_section("ADC sample (0x03)", go)


def dump_power(mcu):
    def go():
        snapshot = mcu.read_power()
        print(f"  generation:       {snapshot.generation}")
        print(f"  fsm state:        {_enum(snapshot.fsm_state)}")
        print(f"  flags:            {_flags(snapshot.flags)}")
        print(f"  live PG mask:     0x{snapshot.live_pg_mask:08X}")
        print(f"  expected PG mask: 0x{snapshot.expected_pg_mask:08X}")
        print(f"  ignore mask:      0x{snapshot.software_ignore_mask:08X}")
        print(f"  failed mask:      0x{snapshot.failed_mask:08X}")
        print(f"  supply count:     {snapshot.supply_count}")
        for i, state in enumerate(snapshot.supply_states[:snapshot.supply_count]):
            print(f"    supply[{i:2d}] = {_enum(state)}")
    return _run_section("Power (0x01)", go)


def dump_alarm(mcu):
    def go():
        snapshot = mcu.read_alarm()
        print(f"  temp task state:      {_enum(snapshot.temp_task_state)}")
        print(f"  voltage task state:   {_enum(snapshot.voltage_task_state)}")
        print(f"  temp status:          {_flags(snapshot.temp_status, 8)}")
        print(f"  temp warn latch:      {_flags(snapshot.temp_warn_latch, 8)}")
        print(f"  voltage alarm gen:    0x{snapshot.voltage_alarm_general:02X}")
        print(f"  voltage alarm F1:     0x{snapshot.voltage_alarm_f1:02X}")
        print(f"  voltage alarm F2:     0x{snapshot.voltage_alarm_f2:02X}")
    return _run_section("Alarm (0x02)", go)


def dump_persistent_log(mcu):
    def go():
        info = mcu.read_persistent_log_info()
        print(f"  format version:      {info.format_version}")
        print(f"  capacity (words):    {info.capacity_words}")
        print(f"  generation:          {info.generation}")
        print(f"  latest error code:   0x{info.latest_error_code:08X}")
        print(f"  continuation count:  {info.continuation_count}")
        entries = mcu.read_persistent_log_entries()
        nonzero = sum(1 for word in entries if word not in (0x00000000, 0xFFFFFFFF))
        print(f"  {nonzero}/{len(entries)} entries look non-empty (raw scan, unfiltered)")
    return _run_section("Persistent log (0x30/0x31, deferred)", go)


SECTIONS = (
    dump_system,
    dump_power,
    dump_alarm,
    dump_config,
    dump_runtime,
    dump_adc,
    dump_persistent_log,
)


class _CaptureBuffer(io.StringIO):
    """StringIO that reports the real stdout's encoding, so the warning
    marker warn() picks for the capture also prints to that stdout."""

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
    add_out_dir_arg(parser, "cm<boardid>_mcu.txt")
    add_common_args(parser)
    args = parser.parse_args()

    reg = make_registry(args)
    board_lines, board_id = board_info(reg)
    board_lines = board_lines + state_lines(reg)
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
    return 1 if dump_mcu(reg, args, board_lines, board_id) else 0


if __name__ == "__main__":
    sys.exit(main())
