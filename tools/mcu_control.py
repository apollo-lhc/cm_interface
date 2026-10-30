#!/usr/bin/env python3
"""Write to the command-module MCU (``MC 0``): Control-page commands, the
ProgCom power inhibit, and the alarm thresholds on the Config page.

Every action asks for confirmation (``--yes`` skips it). Config writes persist
to EEPROM. With no action, prints this help. For reads use ``cm-mcu-dump``;
to set the RTC, ``cm-mcu-set-rtc``.

Usage:
    cm-mcu-control --send-control CLEAR_ALARM_LATCHES [--yes]
    cm-mcu-control --power-cycle [--yes]
    cm-mcu-control --set-alarm-temp FPGA 85 [--yes]
    cm-mcu-control --set-volt-threshold 5 [--yes]
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

from cm_interface.device.mcu import (
    ALARM_TEMP_MAX_C,
    ALARM_TEMP_MIN_C,
    ALARM_VOLT_CPCT_MAX,
    ALARM_VOLT_CPCT_MIN,
    AlarmTempDevice,
    McuControlCommand,
)
from cm_interface.tools._common import add_common_args, make_registry
from cm_interface.tools.mcu_dump import EXPECTED_HARDWARE_ERRORS, _format_error, _warn

# One line per McuControlCommand, for --help (MCU_REGISTER_MAP.md, page 0x7f).
COMMAND_HELP = {
    McuControlCommand.ASSERT_PROGCOM_POWER_INHIBIT:
        "turn board power off and hold it off until released (separate from CLI 'pwr off')",
    McuControlCommand.RELEASE_PROGCOM_POWER_INHIBIT: "release the ProgCom power inhibit",
    McuControlCommand.CLEAR_POWER_FAULT: "clear a latched power fault (CLI 'pwr clearfail')",
    McuControlCommand.CLEAR_ALARM_LATCHES:
        "clear the temperature and voltage alarm latches (CLI 'alm clear')",
    McuControlCommand.ZYNQMON_ENABLE_TRANSMIT: "start ZynqMon telemetry to the Zynq (CLI 'zmon on')",
    McuControlCommand.ZYNQMON_DISABLE_TRANSMIT:
        "stop ZynqMon telemetry (CLI 'zmon off'); sticky until 'zmon on' or reboot",
}


def _commands_epilog():
    width = max(len(c.name) for c in McuControlCommand)
    return "COMMANDs for --send-control:\n" + "\n".join(
        "  %-*s  %s" % (width, c.name, COMMAND_HELP.get(c, "")) for c in McuControlCommand)


def send_control(mcu, command_name, assume_yes):
    command = McuControlCommand[command_name]
    if not assume_yes:
        if command is McuControlCommand.ZYNQMON_DISABLE_TRANSMIT:
            print("WARNING: ZYNQMON_DISABLE_TRANSMIT is sticky. Nothing re-enables "
                  "telemetry except the CLI ('zmon on') or a reboot, so the "
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
        _warn(_format_error(exc))


_CONFIG_WRITE_WARNING = (
    "This value PERSISTS to EEPROM and survives reboot. Raising a temperature "
    "threshold reduces thermal protection; lowering one can force an immediate "
    "power-down that the ProgCom inhibit bit does not reflect."
)


def _confirm_config_write(what, assume_yes):
    if assume_yes:
        return True
    print("WARNING: " + _CONFIG_WRITE_WARNING)
    reply = input(f"Write {what} to live hardware? [y/N] ")
    if reply.strip().lower() != "y":
        print("Aborted.")
        return False
    return True


def set_alarm_temp(mcu, device_name, celsius, assume_yes):
    device = AlarmTempDevice[device_name]
    try:
        before = mcu.read_alarm_config()
        if not _confirm_config_write(f"{device.name} alarm threshold = {celsius} C", assume_yes):
            return
        mcu.set_alarm_temperature(device, celsius)
        after = mcu.read_alarm_config()
        field = "alarm_temp_" + device.name.lower()
        print(f"{device.name} threshold: {getattr(before, field)} -> {getattr(after, field)} C")
    except EXPECTED_HARDWARE_ERRORS as exc:
        _warn(_format_error(exc))


def set_volt_threshold(mcu, percent, assume_yes):
    try:
        before = mcu.read_alarm_config()
        if not _confirm_config_write(f"voltage threshold = {percent} %", assume_yes):
            return
        mcu.set_alarm_voltage_threshold_percent(percent)
        after = mcu.read_alarm_config()
        print("Voltage threshold: {} -> {} %".format(
            before.alarm_volt_threshold_percent, after.alarm_volt_threshold_percent))
    except EXPECTED_HARDWARE_ERRORS as exc:
        _warn(_format_error(exc))


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
        _warn(_format_error(exc))
    finally:
        # A timeout may occur after firmware has accepted ASSERT, so always
        # attempt RELEASE after issuing it, including on Ctrl-C during sleep.
        try:
            mcu.send_control(McuControlCommand.RELEASE_PROGCOM_POWER_INHIBIT)
            print("Released ProgCom power inhibit.")
        except EXPECTED_HARDWARE_ERRORS as exc:
            _warn(f"Could not release ProgCom power inhibit: {_format_error(exc)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=_commands_epilog(),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--send-control", metavar="COMMAND",
                           choices=[c.name for c in McuControlCommand],
                           help="send one Control-page command (listed below)")
    operation.add_argument("--power-cycle", action="store_true",
                           help="assert the ProgCom power inhibit for 15 seconds, "
                           "then release it")
    operation.add_argument("--set-alarm-temp", nargs=2, metavar=("DEVICE", "CELSIUS"),
                           help="write one over-temperature threshold ({} {}..{} C; "
                           "DEVICE is one of {}); persists to EEPROM".format(
                               "whole degrees", ALARM_TEMP_MIN_C, ALARM_TEMP_MAX_C,
                               ", ".join(d.name for d in AlarmTempDevice)))
    operation.add_argument("--set-volt-threshold", type=float, metavar="PCT",
                           help="write the voltage-alarm threshold ({}..{} %%); "
                           "persists to EEPROM".format(
                               ALARM_VOLT_CPCT_MIN / 100.0, ALARM_VOLT_CPCT_MAX / 100.0))
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt for a mutating action")
    args = parser.parse_args()
    if args.set_alarm_temp:
        if args.set_alarm_temp[0] not in AlarmTempDevice.__members__:
            parser.error("DEVICE must be one of " + ", ".join(AlarmTempDevice.__members__))
        try:
            args.set_alarm_temp[1] = int(args.set_alarm_temp[1])
        except ValueError:
            parser.error("CELSIUS must be a whole number")

    if not (args.send_control or args.power_cycle or args.set_alarm_temp
            or args.set_volt_threshold is not None):
        parser.print_help()
        return 2

    mcu = make_registry(args).get_mcu()

    if args.send_control:
        send_control(mcu, args.send_control, args.yes)
        return
    if args.power_cycle:
        power_cycle(mcu, args.yes)
        return
    if args.set_alarm_temp:
        set_alarm_temp(mcu, args.set_alarm_temp[0], args.set_alarm_temp[1], args.yes)
        return
    if args.set_volt_threshold is not None:
        set_volt_threshold(mcu, args.set_volt_threshold, args.yes)
        return


if __name__ == "__main__":
    sys.exit(main())
