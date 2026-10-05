#!/usr/bin/env python
"""
Exercise the command-module MCU's ProgCom register area (``MC 0``) on real
hardware, following ``MCU_REGISTER_MAP.md``.

Reads every implemented page (System, Power, Alarm, ADC) and reports each
one independently, so a page the deployed firmware doesn't yet implement
(``invalid MCU page``) doesn't stop the rest. The deferred persistent-log
pages are read as a best effort. The write-only Control page is only
touched if you explicitly pass ``--send-control`` or ``--power-cycle`` and
confirm.

Usage:
    ./exercise_mcu.py [--dev-path /dev/ttyUL4] [--debug]
    ./exercise_mcu.py --send-control CLEAR_ALARM_LATCHES [--yes]
    ./exercise_mcu.py --power-cycle [--yes]
"""

import argparse
import os
import sys
import time

# Permit direct execution from either the repository root (via its symlink)
# or this package directory, without requiring an installation step.
if not __package__:
    _package_dir = os.path.dirname(os.path.realpath(__file__))
    _package_parent = os.path.dirname(_package_dir)
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from cm_interface.errors import CMError, McuCapabilityUnavailable
from cm_interface.registry import Registry
from cm_interface.device.mcu import McuControlCommand, describe_reset_cause


# RuntimeError is here on purpose: _run_section is a per-section warning
# boundary, so a stray RuntimeError from any layer (e.g. a map-version mismatch
# against old firmware) must degrade to a warning, not skip the later sections.
EXPECTED_HARDWARE_ERRORS = (CMError, OSError, TimeoutError, ValueError, RuntimeError)


def _format_error(exc):
    """Return a compact message that includes useful chained exceptions."""
    messages = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip() or type(current).__name__
        if message not in messages:
            messages.append(message)
        current = current.__cause__ or current.__context__
    return " (caused by: ".join(messages) + ")" * (len(messages) - 1)


def _run_section(title, fn):
    print(f"\n--- {title} " + "-" * max(0, 50 - len(title)))
    try:
        fn()
    except McuCapabilityUnavailable:
        print("  not advertised by firmware")
    except EXPECTED_HARDWARE_ERRORS as exc:
        print(f"  ⚠ {_format_error(exc)}")


def exercise_system(mcu):
    def go():
        info = mcu.system_info
        print(f"  map version:      {info.map_major}.{info.map_minor}")
        print(f"  hardware rev:     {info.hardware_revision}")
        print(f"  board id:         0x{info.board_id:08X}")
        print(f"  uptime:           {info.uptime_seconds} s")
        print(f"  reset cause:      0x{info.reset_cause:08X}")
        for detail in describe_reset_cause(info.reset_cause):
            print(f"    - {detail}")
        print(f"  git version:      {info.git_version!r}")
        print(f"  capabilities:     {info.capabilities!r}")
        print(f"  health:           {info.health!r}")
        if info.ff_user_mask is None:
            for label in ("ff user mask", "ff present mask", "build type",
                          "build time"):
                print(f"  {label + ':':<17s} (requires map minor >= 1)")
        else:
            print(f"  ff user mask:     0x{info.ff_user_mask:08X}")
            print(f"  ff present mask:  0x{info.ff_present_mask:08X}")
            print(f"  build type:       {info.build_type!r}")
            print(f"  build time:       {info.build_time!r}")
    _run_section("System (0x00)", go)


def exercise_config(mcu):
    def go():
        cfg = mcu.read_alarm_config()
        print(f"  temp threshold FF:    {cfg.alarm_temp_ff} C")
        print(f"  temp threshold DCDC:  {cfg.alarm_temp_dcdc} C")
        print(f"  temp threshold TM4C:  {cfg.alarm_temp_tm4c} C")
        print(f"  temp threshold FPGA:  {cfg.alarm_temp_fpga} C")
        print(f"  voltage threshold:    {cfg.alarm_volt_threshold_percent:.2f} %")
    _run_section("Config (0x05, read-only)", go)


def exercise_runtime(mcu):
    def go():
        info = mcu.read_runtime()
        print(f"  heap free/min/total: {info.heap_free_bytes} / "
              f"{info.heap_min_ever_free_bytes} / {info.heap_total_bytes} bytes")
        print(f"  system stack:        {info.system_stack_untouched_words} of "
              f"{info.system_stack_total_words} words untouched")
        print(f"  zynqmon transmit:    {info.zynqmon_transmit_enabled}")
        print(f"  fpga done:           {info.fpga_done!r}")
        print(f"  rtc:                 {info.rtc.isoformat()}")
    _run_section("Runtime (0x06)", go)


def exercise_adc(mcu):
    def go():
        snapshot = mcu.read_adc()
        for reading in snapshot.readings:
            flag = "" if reading.valid else "  (NaN / unpublished)"
            print(f"  {reading.index:2d} {reading.name:<16s} {reading.value:10.4f}{flag}")
    _run_section("ADC sample (0x03)", go)


def exercise_power(mcu):
    def go():
        snapshot = mcu.read_power()
        print(f"  generation:       {snapshot.generation}")
        print(f"  fsm state:        {snapshot.fsm_state!r}")
        print(f"  flags:            {snapshot.flags!r}")
        print(f"  live PG mask:     0x{snapshot.live_pg_mask:08X}")
        print(f"  expected PG mask: 0x{snapshot.expected_pg_mask:08X}")
        print(f"  ignore mask:      0x{snapshot.software_ignore_mask:08X}")
        print(f"  failed mask:      0x{snapshot.failed_mask:08X}")
        print(f"  supply count:     {snapshot.supply_count}")
        for i, state in enumerate(snapshot.supply_states):
            print(f"    supply[{i:2d}] = {state!r}")
    _run_section("Power (0x01)", go)


def exercise_alarm(mcu):
    def go():
        snapshot = mcu.read_alarm()
        print(f"  temp task state:      {snapshot.temp_task_state!r}")
        print(f"  voltage task state:   {snapshot.voltage_task_state!r}")
        print(f"  temp status:          {snapshot.temp_status!r}")
        print(f"  temp warn latch:      {snapshot.temp_warn_latch!r}")
        print(f"  voltage alarm gen:    0x{snapshot.voltage_alarm_general:02X}")
        print(f"  voltage alarm F1:     0x{snapshot.voltage_alarm_f1:02X}")
        print(f"  voltage alarm F2:     0x{snapshot.voltage_alarm_f2:02X}")
    _run_section("Alarm (0x02)", go)


def exercise_persistent_log(mcu):
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
    _run_section("Persistent log (0x30/0x31, deferred)", go)


def send_control(mcu, command_name, assume_yes):
    command = McuControlCommand[command_name]
    if not assume_yes:
        if command is McuControlCommand.ZYNQMON_DISABLE_TRANSMIT:
            print("WARNING: ZYNQMON_DISABLE_TRANSMIT is sticky. Nothing re-enables "
                  "telemetry except the CLI ('zmon enable') or a reboot, so the "
                  "blade stays dark to the Zynq if this client exits or loses its link.")
        reply = input(
            f"Send Control command {command.name} (0x{int(command):02X}) to "
            f"live hardware? [y/N] "
        )
        if reply.strip().lower() != "y":
            print("Aborted.")
            return
    try:
        mcu.send_control(command)
        print(f"Sent {command.name}.")
    except EXPECTED_HARDWARE_ERRORS as exc:
        print(f"  ⚠ {_format_error(exc)}")


def power_cycle(mcu, assume_yes):
    """Inhibit board power for 15 seconds, then always release the inhibit."""
    if not assume_yes:
        reply = input(
            "Assert the MCU ProgCom power inhibit for 15 seconds, then release "
            "it? [y/N] "
        )
        if reply.strip().lower() != "y":
            print("Aborted.")
            return

    print("Asserting ProgCom power inhibit.")
    try:
        mcu.send_control(McuControlCommand.ASSERT_PROGCOM_POWER_INHIBIT)
        print("Power inhibited; waiting 15 seconds.")
        time.sleep(15)
    except EXPECTED_HARDWARE_ERRORS as exc:
        print(f"  ⚠ {_format_error(exc)}")
    finally:
        # A timeout may occur after firmware has accepted ASSERT, so always
        # attempt RELEASE after issuing it, including on Ctrl-C during sleep.
        try:
            mcu.send_control(McuControlCommand.RELEASE_PROGCOM_POWER_INHIBIT)
            print("Released ProgCom power inhibit.")
        except EXPECTED_HARDWARE_ERRORS as exc:
            print(f"  ⚠ Could not release ProgCom power inhibit: {_format_error(exc)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-path", default="/dev/ttyUL4",
                         help="serial device path (default: /dev/ttyUL4)")
    parser.add_argument("--debug", action="store_true",
                         help="log raw UART transactions to stdout")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--send-control", metavar="COMMAND",
                           choices=[c.name for c in McuControlCommand],
                           help="send one write-only Control-page command instead "
                           "of reading pages")
    operation.add_argument("--power-cycle", action="store_true",
                           help="assert the ProgCom power inhibit for 15 seconds, "
                           "then release it")
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt for a control action")
    args = parser.parse_args()

    reg = Registry(dev_path=args.dev_path,
                    debug=sys.stdout if args.debug else None)
    mcu = reg.get_mcu()

    if args.send_control:
        send_control(mcu, args.send_control, args.yes)
        return
    if args.power_cycle:
        power_cycle(mcu, args.yes)
        return

    exercise_system(mcu)
    exercise_power(mcu)
    exercise_alarm(mcu)
    exercise_config(mcu)
    exercise_runtime(mcu)
    exercise_adc(mcu)
    exercise_persistent_log(mcu)
    print()


if __name__ == "__main__":
    main()
