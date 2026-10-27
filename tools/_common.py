"""Argument and output conventions shared by the command-line tools.

Every tool takes --dev-path and --debug. Tools that work on named devices take
the names as positional arguments or --all; dumps need one of the two, checks
default to every device. Dumps take --out-dir: with it, each entry goes to its
own cm<boardid>_<name>.txt and stdout shows only the 'wrote' and result lines;
without it, everything is printed. Checks end with print_summary().
"""
import os
import sys

from cm_interface.device.mcu import PowerFsmState
from cm_interface.registry import Registry

DEFAULT_DEV_PATH = "/dev/ttyUL4"


def add_common_args(parser):
    parser.add_argument("--dev-path", default=DEFAULT_DEV_PATH,
                        help="UART device (default: %(default)s)")
    parser.add_argument("--debug", action="store_true",
                        help="log every UART transaction to stdout")


def make_registry(args, **kwargs):
    return Registry(dev_path=args.dev_path,
                    debug=sys.stdout if args.debug else None, **kwargs)


def add_device_args(parser, what, names, default_all=False):
    """Positional device names plus --all. Names are checked in
    selected_devices(), not by argparse: nargs='*' with choices rejects an
    empty list on older Pythons."""
    parser.add_argument("device", nargs="*", metavar="NAME",
                        help="%s name(s)%s, given together (not split around "
                             "options): %s" % (
                            what, " (default: all)" if default_all else "",
                            ", ".join(names)))
    parser.add_argument("--all", action="store_true", help="every %s" % what)


def selected_devices(parser, args, names, default_all=False):
    """Return the device names to work on, in the order given (or names order
    for --all). Exits through parser.error on bad input."""
    unknown = [d for d in args.device if d not in names]
    if unknown:
        parser.error("unknown name(s): %s (choose from %s)"
                     % (", ".join(unknown), ", ".join(names)))
    if args.all and args.device:
        parser.error("give names or --all, not both")
    if args.all or (default_all and not args.device):
        return list(names)
    if not args.device:
        parser.error("give one or more names, or --all")
    return list(dict.fromkeys(args.device))


def add_out_dir_arg(parser, file_name):
    parser.add_argument("--out-dir", metavar="DIR",
                        help="write to DIR instead of stdout, as %s (existing files "
                             "are overwritten; board id is 'unknown' if the MCU "
                             "can't report it)" % file_name)


def out_path(out_dir, board_id, name):
    """DIR/cm<boardid>_<name>.txt; '/' in a name (3V3/1V8) becomes '-'."""
    return os.path.join(out_dir, "cm%s_%s.txt" % (
        "unknown" if board_id is None else board_id, name.replace("/", "-")))


def write_lines(path, lines, failed=False):
    # utf-8 explicitly: Python 3.6 under a C locale would otherwise use ASCII.
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote %s%s" % (path, "  (read failures)" if failed else ""))


def board_info(reg):
    """Return (board header lines, board id or None). Never fatal: old firmware
    or a missing MCU capability just yields 'unavailable'."""
    try:
        info = reg.get_mcu().system_info
    except Exception as exc:
        return ["  board id:  unavailable (%s)" % exc], None
    return [
        "  board id:  %d" % info.board_id,
        "  hw rev:    %d" % info.hardware_revision,
        "  firmware:  %s" % info.git_version,
    ], info.board_id


def power_is_on(reg):
    """Return False, after printing why, if the MCU power state machine is not
    in POWER_ON. If the state can't be read (e.g. old firmware), print a
    warning and return True so the tool still runs."""
    try:
        state = reg.get_mcu().read_power().fsm_state
    except Exception as exc:
        print("MCU power state: unknown (%s); continuing" % exc)
        return True
    if state != PowerFsmState.POWER_ON:
        print("power is off: MCU power state machine is %s, not POWER_ON; "
              "nothing read" % state.name)
        return False
    return True


def _cell(row, check):
    if check not in row:
        return "-"
    return "OK" if row[check] else "FAIL"


def print_summary(names, checks, results):
    """Print a device x check table and the overall result; return True if
    everything passed. results[name] maps check -> pass/fail; a check missing
    from it did not run ("-") and fails the device."""
    width = max([len(n) for n in names] + [6])
    print("\nsummary")
    print("  %-*s  %s  result" % (width, "device", "".join("%-8s" % c for c in checks)))
    all_ok = True
    for name in names:
        row = results.get(name, {})
        device_ok = all(row.get(c) for c in checks)
        all_ok = all_ok and device_ok
        print("  %-*s  %s  %s" % (width, name,
                                  "".join("%-8s" % _cell(row, c) for c in checks),
                                  "OK" if device_ok else "FAIL"))
    print("result: %s" % ("OK" if all_ok else "PROBLEMS FOUND"))
    return all_ok
