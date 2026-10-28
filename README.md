# cm_interface

Python framework for UART‑based management of the command module's clocks,
Firefly modules, power supplies, MCU, and FPGA generic interfaces.

## Overview
The library abstracts the low‑level UART serial protocol and provides a typed, object‑oriented view of each hardware block. Devices are discovered and instantiated through a singleton `Registry`.

## Package layout
```
cm_interface/
├─ __init__.py
├─ compat.py              # Python 3.6 compatibility helpers
├─ uart.py                # Lazy UART wrapper (pySerial) with debug logging
├─ errors.py              # Exception hierarchy
├─ utils.py               # PMBus Linear11/Linear16 codecs (crc8/pack_uint16 are unused)
├─ registry.py            # Global Registry for board devices
├─ MCU_REGISTER_MAP.md    # MCU wire contract (with MCU_Reg.h)
├─ TODO.md                # Open, deferred and dropped cm_interface work
├─ FUTURE_IDEAS.md        # Ideas not yet planned
├─ core_config.py         # Fixed UART addresses for clocks and LGA80D
├─ firefly_presets.py     # Board-specific Firefly layouts (TF, IT-DTC)
├─ device/
│  ├─ __init__.py
│  ├─ base.py             # Abstract Device class
│  ├─ clock.py            # Shared clock base (SiClock, ClockHealth)
│  ├─ si5395.py           # Si5395 clock (all positions on Rev3)
│  ├─ si5341.py           # Si5341 clock (R0A on Rev2)
│  ├─ firefly.py          # Tx, Rx, and 4‑channel firefly classes
│  ├─ lga80d.py           # LGA80D DC-DC converter implementation
│  ├─ mcu.py              # Command-module MCU register interface
│  └─ fpga.py             # Raw F1/F2 generic register interface
├─ tools/                 # Command-line tools: run ./tools/NAME.py from a checkout; the RPM installs cm-<name>
│  ├─ _common.py          # Shared conventions: NAME.../--all, --out-dir, --dev-path, --debug, check summary
│  ├─ clock_checks.py     # cm-clock-checks: read-only Si5395/Si5341 status checks
│  ├─ clock_dump.py       # cm-clock-dump: clock register dump (--out-dir)
│  ├─ exercise_mcu.py     # cm-exercise-mcu: exercise the MCU pages; writes/control prompt unless --yes
│  ├─ ff_cdr.py           # cm-ff-cdr: report/enable/disable Firefly CDR per channel
│  ├─ ff_rx_eq.py         # cm-ff-rx-eq: report/set Firefly Rx amplitude and pre-emphasis per channel
│  ├─ ff_status.py        # cm-ff-status: Firefly status summary (--out-dir)
│  ├─ lga80d_checks.py    # cm-lga80d-checks: read-only LGA80D frequency/temperature/timing checks
│  ├─ lga80d_dump.py      # cm-lga80d-dump: LGA80D register dump (--snapshot adds the SNAPSHOT read)
│  ├─ lga80d_status.py    # cm-lga80d-status: decoded LGA80D status registers (--out-dir)
│  ├─ mcu_dump.py         # cm-mcu-dump: read-only MCU page dump (--out-dir)
│  ├─ mcu_set_rtc.py      # cm-mcu-set-rtc: set the MCU RTC from the host clock
│  ├─ vccint_2phase.py    # VCCINT 2-phase experiment (checkout only; not in the RPM)
│  └─ examples.py         # Worked examples (see EXAMPLES.md; not installed in /usr/bin)
├─ tests/                 # FakeUART unit tests; tests/hw/ is the opt-in hardware suite
└─ registers/
   ├─ si5395.json         # Si5395 register map (page-based)
   ├─ firefly12.json      # Firefly 12-channel register map
   ├─ firefly4.json       # Firefly 4-channel register map
   ├─ firefly_cernb.json  # Firefly CERN-B variant register map
   └─ lga80d.json         # LGA80D PMBus commands
```

## Installation
```bash
pip install pyserial  # required for UART access
```
The package supports Python 3.6 and newer and has no external dependency beyond
`pyserial`. On Python 3.6 it automatically uses an internal frozen-record
fallback, so the `dataclasses` backport does not need to be installed on the
target Zynq system.

The `RPM` GitHub workflow builds an Enterprise Linux 8 package on pushes to
`main` and when a GitHub release is created. Only release builds upload the
binary RPM and source RPM as release assets. Use release tags of the form
`v1.2.3` or `1.2.3`; push builds use version `0.0.0`. Each RPM release number
includes the workflow run number and commit ID. The binary package is
`python3-cm-interface`, targets the EL8 system Python 3.6, and includes the
register JSON maps. Install a downloaded binary RPM with
`sudo dnf install ./python3-cm-interface-*.noarch.rpm`.

The RPM also installs the command-line tools as `cm-<name>` (for example
`cm-lga80d-dump --all`). The same tool runs from a checkout as
`./tools/lga80d_dump.py --all`; the `cm-*` names exist only after the RPM is
installed. See [Command-line tools](#command-line-tools) for each tool's usage.

## Command-line tools

The RPM installs these as `cm-<name>` in `/usr/bin`. Every tool has `--help`
with the full details (register addresses, datasheet tables, caveats).

### Common options

| Option | Meaning |
|---|---|
| `NAME ...` / `--all` | Devices to work on. Give names together, not split around options (`cm-ff-status F1_1 F2_6 --out-dir d`, not `cm-ff-status F1_1 --out-dir d F2_6`). Dumps and Firefly tools need one or the other; checks default to all. |
| `--out-dir DIR` | Dumps only: write `DIR/cm<boardid>_<name>.txt` instead of printing; stdout then shows only the `wrote` lines. Existing files are overwritten. |
| `--force` | Clock and Firefly tools only: run even if the MCU power state machine is not `POWER_ON` (see below). |
| `--dev-path DEV` | ProgCom UART (default `/dev/ttyUL4`). |
| `--debug` | Print every UART transaction. |

**Power gate.** The clock and Firefly tools (`cm-clock-checks`,
`cm-clock-dump`, `cm-ff-status`, `cm-ff-cdr`, `cm-ff-rx-eq`) read the MCU
power state first. If it is not `POWER_ON` they print
`power is off: ... nothing read` and exit 1; `--force` reads anyway. If the
state can't be read (old firmware) they run. `cm-clock-dump`, `cm-ff-status`
and `cm-lga80d-dump` record the power state, with any latched fault flags, in
their header. The LGA80D tools are not gated:
the modules answer PMBus with the board off.

The dump, check and Firefly tools exit 1 if any read or write failed, or a
check failed.

### Clocks

| Command | What it does |
|---|---|
| `cm-clock-checks` | Read-only Si5395/Si5341 checks: `id`, `status` (live LOS/OOF/LOL/holdover), `flags` (sticky). Alarms the configuration explains (disabled input, free run) are shown but don't fail. Ends with a clock x check OK/FAIL table. |
| `cm-clock-dump` | Read-only summary dump per clock: status, outputs with R dividers, P/M/N dividers and derived frequencies. |

```
cm-clock-checks                          # all checks, all clocks
cm-clock-checks R0A R1B --check flags
cm-clock-dump --all --out-dir DIR
cm-clock-dump R0A --hw-rev 2             # board EEPROM not programmed
```

Clock names: `R0A R0B R1A R1B R1C`. The chip at each position comes from the
board revision the MCU reports; `--hw-rev N` overrides it.

### Fireflies

Firefly locations are `F1_1` ... `F2_7`; which exist depends on the board
revision. A location selects both halves (Tx and Rx) of an x12 pair. Which
modules are fitted comes from the MCU's present mask, so `--all` means every
populated module on this board. A module not in the MCU's user mask is listed
but not accessed.

| Command | What it does |
|---|---|
| `cm-ff-status` | Read-only status per module: part, serial, temperature, Vcc, decoded alarm latches (LOS, faults, CDR loss of lock, Rx power). |
| `cm-ff-cdr` | Report or change CDR enable per channel. **Writes** with `--enable`/`--disable`. |
| `cm-ff-rx-eq` | Report or change Rx output amplitude and pre-emphasis per channel. **Writes** with `--amplitude`/`--preemphasis`. |

```
cm-ff-status --all
cm-ff-status F1_1 F2_6 --out-dir DIR

cm-ff-cdr --all                                      # report
cm-ff-cdr F1_1 --side rx --disable --channels 3,10
cm-ff-cdr F2_6 --side tx --enable --channels 2
cm-ff-cdr F1_1 F1_2 --enable                         # all channels, both sides

cm-ff-rx-eq --all                                    # report
cm-ff-rx-eq F1_1 --amplitude 5 --channels 1,3,5-8
cm-ff-rx-eq F1_1 --preemphasis 3 --channels 12
cm-ff-rx-eq F2_6 --preemphasis 3                     # all 4 channels of an x4
```

Per-channel changes:

- `--channels` takes a comma list with ranges (`1,3,5-8`); without it the
  change goes to every channel. It only applies together with a change
  option; a report always shows every channel.
- Channels are 1-based: 1-12 on x12, 1-4 on x4. The x12 datasheet's Rx00-Rx11
  are channels 1-12.
- One run sets one value. For different values on different channels, run
  once per value.
- `--side tx|rx` (`cm-ff-cdr`) limits the change and report to one side.
- Each changed module is read back and the read-back is printed.

Values are the raw 4-bit codes; the report shows what each means.

| | x12 (25G Rx) | x4 |
|---|---|---|
| Amplitude | 0-15: 80 mV + 76 mV/step, 7-15 = 612 mV (default 4 = 384 mV) | 0-3: 300, 450, 600, 900 mV (default 0) |
| Pre-emphasis | 0-15: 4.8 % of amplitude per step, 7-15 = 33.6 % (default 2 = 9.6 %) | 0-7: 0-6 dB, 7 = 7.5 dB (default 0) |

Caveats: the x4 datasheet calls the amplitude a global parameter for the four
channels, so a per-channel amplitude may not take effect independently.
CERN-B and 14G x12 modules are skipped by `cm-ff-cdr` and `cm-ff-rx-eq`.
If one module fails (for example `--channels 10` on an x4), the others are
still changed and the exit status is 1.

### LGA80D DC-DC converters

Supply names: `3V3/1V8 F1VCCINT1 F1VCCINT2 F2VCCINT1 F2VCCINT2 F1AVTT/CC F2AVTT/CC`.

| Command | What it does |
|---|---|
| `cm-lga80d-status` | Read-only decoded PMBus status registers (STATUS_WORD, VOUT/IOUT/INPUT/TEMP/CML/MFR). |
| `cm-lga80d-dump` | Read-only dump of every readable PMBus register, both output pages. `--snapshot` also captures the 32-byte SNAPSHOT through the MCU (not a pure read; needs firmware with the SN device). |
| `cm-lga80d-checks` | Read-only checks: `freq` (did LGA80D_init take effect; run powered on), `temp` (die vs DrMOS temperature; run under load), `timing` (turn-on/off settings vs the firmware's PG check). |

```
cm-lga80d-status --all
cm-lga80d-dump F1VCCINT1 --page 0
cm-lga80d-dump --all --out-dir DIR
cm-lga80d-dump F1VCCINT1 --snapshot
cm-lga80d-checks --check freq
cm-lga80d-checks F1VCCINT2 --check temp
```

`cm-lga80d-dump --include-unconfirmed` also reads registers the datasheet
doesn't list as supported; that can set the "invalid command" bit in
STATUS_CML.

### MCU

| Command | What it does |
|---|---|
| `cm-mcu-dump` | Read-only dump of the MCU's ProgCom pages (System, Power, Alarm, Config, Runtime, ADC). Uptime, RTC, heap/stack and ADC values change on every read. |
| `cm-mcu-set-rtc` | Set the MCU RTC to this host's local time, then read it back. **Writes.** Check the host clock first. |
| `cm-exercise-mcu` | Read every MCU page and report each independently. With an option it **writes** and asks for confirmation (`--yes` skips it). |

```
cm-mcu-dump --out-dir DIR
cm-mcu-set-rtc --dry-run                 # show what would be written
cm-mcu-set-rtc
cm-exercise-mcu
cm-exercise-mcu --send-control CLEAR_ALARM_LATCHES
cm-exercise-mcu --power-cycle            # power inhibit for 15 s
cm-exercise-mcu --set-alarm-temp FPGA 85 # persists to EEPROM; 50-100 C
cm-exercise-mcu --set-volt-threshold 5   # persists to EEPROM; 1-10 %
```

## Basic usage
```python
from cm_interface.registry import Registry

# TF board using the default device (/dev/ttyUL4)
reg = Registry(setup="tf")

# Use dev_path="/dev/ttyUSB0" in the call above for a different UART.
# Registry is a singleton, so choose setup and dev_path on its first creation.

# Clock example
clk = reg.get_clock('R0A')
print('Mode:', clk.mode)
print('Health:', clk.health)

# The TF preset has one populated 4-channel transceiver, at F2_6.
xf = reg.get_firefly('F2_6')
print('Transceiver part:', xf.part_id)

# LGA80D example
lga = reg.get_lga80d('F1VCCINT1')
print('Voltage:', lga.voltage, 'V')
print('Telemetry:', lga.read_telemetry(page=0))
status = lga.read_status(page=0)
print('Supply status:', 'FAULT' if status.has_faults else 'OK')
print(f'STATUS_WORD=0x{status.word:04X}')

# Raw FPGA generic-port access. Reads return bytes in wire order.
f1 = reg.fpgas['F1']
raw = f1.read_reg(0x20, size=4)
f1.write_reg(0x00, b'\x78\x56\x34\x12')

# Integer writes require an explicit width and use little-endian encoding.
f1.write_reg(0x00, 0x12345678, size=4)
```

## MCU endpoint status

`Registry` always exposes the command-module MCU as `reg.mcu` and through
`reg.get_mcu()`. The current Python client implements UART7 Phases 1, 2a and 2b
for ProgCom device `MC 0` (read pages, the writable Config page and the Control page):

```python
from cm_interface.device.mcu import McuControlCommand

mcu = reg.get_mcu()
info = mcu.system_info
print(info.git_version, info.uptime_seconds, info.capabilities, info.health)

adc = mcu.read_adc()
print(adc["VCC_12V"].value, adc["VCC_12V"].valid)

power = mcu.read_power()
print(power.fsm_state, power.flags, power.supply_states)

alarm = mcu.read_alarm()
print(alarm.temp_task_state, alarm.temp_status)

cfg = mcu.read_alarm_config()          # page 0x05, map minor >= 2
print(cfg.alarm_temp_ff, cfg.alarm_volt_threshold_percent)

# Writes need map minor >= 3 (CONFIG_WRITE). They PERSIST to EEPROM and move the
# over-temperature power-down point. Clamps: 50-100 C, 1-10 %.
from cm_interface.device.mcu import AlarmTempDevice
mcu.set_alarm_temperature(AlarmTempDevice.TM4C, 75)
mcu.set_alarm_voltage_threshold_percent(5.0)

rt = mcu.read_runtime()                # page 0x06, map minor >= 1 firmware
print(rt.heap_free_bytes, rt.system_stack_untouched_words, rt.fpga_done)
print(rt.rtc.isoformat())              # "unset" when the RTC is not valid

# Needs map minor >= 4 (RTC_WRITE). Sets host local wall-clock time; the RTC
# has no time zone. Refuses times before 2026-01-01. CLI: cm-mcu-set-rtc
mcu.set_rtc()

mcu.send_control(McuControlCommand.CLEAR_ALARM_LATCHES)
```

`system_info` validates the `CMCU` magic and map major version 1. On map
minor >= 1 firmware it also returns `ff_user_mask`, `ff_present_mask`,
`build_type` and `build_time`; on minor 0 these are `None` and are not even
requested. ADC values
are 21 little-endian IEEE-754 binary16 values, selectable by channel name; an
unpublished channel reads back as `NaN` rather than carrying a separate
validity bitmap. `read_power()`
uses an even/odd generation counter and retries if publication changes mid-
read, matching the ~25ms control-loop pass firmware updates it in. `read_alarm()`
has no generation counter — its state/status/latch fields are each updated
atomically as a group by firmware. The temperature status/latch pair is an
8-byte `read_block`, which is **two** 4-byte wire transactions rather than
one; the firmware-side grouping still holds, but the pair is not fetched
atomically over the wire.

**Capability gating.** Every page-scoped method checks the firmware capability
mask (read once, cached; `mcu.capabilities(refresh=True)` re-reads it, e.g.
after a reflash) and raises `McuCapabilityUnavailable` instead of issuing a
read the firmware would reject. Pass `check_capability=False` to probe a page
whose bit is not set. `system_info` is never gated: it is how the mask is
discovered.

**Done.** Python client and in-memory unit tests for pages `0x00` System,
`0x01` Power, `0x02` Alarm, `0x03` ADC, `0x05` Config (readable from map
minor 2, writable from minor 3, hardware-verified; see the hazard in
`MCU_REGISTER_MAP.md`), `0x06` Runtime and `0x7f` Control (including the sticky
`ZYNQMON_DISABLE_TRANSMIT`). The matching firmware
(`cm_mcu/projects/cm_mcu/MCU_Reg.c`) serves those pages at map minor 3. Nothing here establishes what is installed on a given
target; older firmware may still return `MCU device not implemented`.

**Outstanding** — tracked in `../MCU_UART7_IMPLEMENTATION_PLAN.md`:

- Phase 3 (deferred; the error log needs rework first, prerequisites B11 and
  B13): the persistent-log pages `0x30`/`0x31`. Their offsets are frozen
  and `read_persistent_log_info()`/`read_persistent_log_entries()` exist, but
  firmware does not serve them — reads return `invalid MCU page`.

`../MCU_CLI_GAP_PLAN.md` records which MCU state is still reachable only from
the interactive CLI. `MCU_REGISTER_MAP.md` and `MCU_Reg.h` are the
authoritative wire contract; the older plans in `../outdated/` are not.

## FPGA endpoint status

`Registry` always exposes raw generic endpoints as `reg.fpgas["F1"]` and
`reg.fpgas["F2"]`, or through `get_fpga()`. They use ProgCom devices `FP 0`
and `FP 1`, page 0, with byte addresses from `0x00` through `0xff`. A single
transaction transfers one to four bytes; `read_block()` performs longer
sequential reads as multiple transactions.

Raw generic register reads and writes through this ProgCom path have been
tested successfully on hardware. The validation covers the Phase-1 transport,
not portable register semantics across different FPGA bitfiles.

The FPGA interface is deliberately unprofiled: it does not assign register
names, units, permissions, or decoding to a bitfile-defined address. F1 and F2
byte writes preserve wire order, while integer writes require an explicit
width and use little-endian encoding. `probe(reg)` returns an
`FPGAProbeResult`; register 0 is the default only because it is a safe scratch
register in the currently deployed frequency-test bitfile. Callers working
with another bitfile must choose a known-safe probe address.

An address-phase I2C NACK reported by the MCU as `ADDR_ACK_ERROR` becomes
`FPGAInterfaceUnavailable`, since a valid bitfile may omit the endpoint.
Data-phase NACKs and ambiguous errors remain ordinary `RegisterAccessError`
failures. The deployed MCU firmware must implement the `FP` transport and the
loaded FPGA bitfile must instantiate the generic I2C slave.

**Done:** Phase 1, the raw `FP` transport, tested on hardware. **Outstanding**
(`../FPGA_GENERIC_INTERFACE_PLAN.md`): bitfile profiles, identity matching,
named registers, permissions, high-level diagnostics and the VU13P GT-test
adapter. Until then raw writes have no semantic safety checks.

## Debug Logging

Enable UART transaction logging for troubleshooting and development:

```python
import sys
from cm_interface.registry import Registry

# Log all UART traffic to stdout
reg = Registry(setup='tf', debug=sys.stdout)

# Or log to a file
with open('uart_debug.log', 'w') as f:
    reg = Registry(setup='tf', debug=f)
    clk = reg.get_clock('R0A')
    device_id = clk.get_device_id()  # Transactions logged to file
```

Debug output format:
```
UART TX (11): r DC 1 0 1\n
UART RX (5): d 7A\n
```

The terminating newline is shown as `\n` so that each transaction remains on
one debug-output line. Non-ASCII traffic is labeled `[hex]` and displayed as
hexadecimal bytes.

**Note:** Debug logging is disabled by default (no performance impact). The UART bridge automatically handles page selection when accessing registers on different pages using the full 16-bit address.

### UART timeout

The default UART read timeout is 5 seconds. Most responses arrive in roughly
16 ms, but a PMBus request can wait about 2.8 seconds while the MCU monitoring
task holds the shared I2C semaphore for a complete power-supply sweep. The
5-second default provides margin for that expected contention. Callers can
override it with `Registry(timeout=...)` when appropriate.

### LGA80D telemetry conventions

`read_switching_frequency()` and the `switching_frequency` field returned by
`read_telemetry()` are in kHz. Unloaded outputs on the current hardware have
historically returned negative `output_current` values. These signed readings
are passed through unchanged; consult `read_status()` to distinguish this
known no-load behavior from a reported supply fault.

`read_status()` returns an `LGA80DStatus` object, not an integer. Its `word`,
`vout`, `iout`, `input`, `temperature`, `cml`, and `manufacturer` fields expose
the raw PMBus status registers; `has_faults` is true if any of the category
bytes are nonzero, or if `word` has any bit set other than `STATUS_WORD` bits
6 (`OFF`) and 11 (`POWER_GOOD#`) -- those two are asserted for normal,
intentionally-disabled states and are excluded so a healthy but powered-off
supply doesn't read as faulted.

`Registry.reset_all_lga80d_snapshots(force=True)` resets the snapshot-history
register on every configured LGA80D (modeled on the MCU firmware's `sn_all`
command). It refuses to do anything unless `force=True` is passed, and even
then unconditionally checks the MCU's power state machine reports exactly
`POWER_OFF` first, raising `RuntimeError` otherwise -- `force` cannot skip
that check, it only unlocks the attempt, since the reset only succeeds while
a supply's output is off. Per-device primitives (`LGA80D.reset_snapshot(page)`
/ `reset_all_snapshots()`) are also available directly if you need to reset
just one supply; they run unconditionally and cannot verify board-level power
state themselves, so prefer the `Registry` method unless you've confirmed
that precondition some other way.

`LGA80D.read_snapshot(page=0)` reads the atomic 32-byte `SNAPSHOT` (PMBus `0xEA`) of one output
page and returns a frozen `LGA80DSnapshot` (input/output voltage, signed output current, max
current, duty cycle, temperature, switching frequency, the seven status bytes, and the raw
bytes). It goes through the MCU's ProgCom `SN` device: one capture write (the MCU runs PAGE,
`SNAPSHOT_CONTROL=0x01`, a ~40 ms wait and the block read, then caches the result), then eight
4-byte reads of the cache. It is read-only; it never erases the snapshot history. The capture
blocks the MCU's ProgCom task for ~70 ms uncontended, typically 200-400 ms while the monitor task is
polling (up to ~0.7 s seen on hardware), and much longer if the I2C bus stays contended, and
the UART timeout is not changed. Failures (including old firmware without `SN`, which answers
`e invalid device type`) raise `RegisterAccessError`. A new unit holds a
factory-qualification fault until it is erased with `reset_snapshot` (output off).
`print(snapshot)` (or `snapshot.format_lines()`) gives a readable summary; `cm-lga80d-dump --snapshot`
uses it. `snapshot.is_stored_record` reads the flash status byte (byte 22, undocumented, so
inferred from the failure-analysis notes): `True` for `0x00` (a fault record is stored, and the
values are that record, not live data), `False` for `0xFF` (erased, values are live), `None` for
anything else.

## Standard board configurations
The repository ships two ready‑to‑use presets that match the real hardware layouts described in `design.md`.

| Config | Description | Clock address base | Firefly layout | Notable variants |
|-------|-------------|-------------------|---------------|------------------|
| **TF** | Track‑Finder board. Only F2_6 is populated among the four 4‑channel transceiver slots; F1_5, F1_6, and F2_5 are empty. F1_1‑F1_4 are standard Rx-only devices and F2_3 is a standard Tx-only device. | R0A‑R1C at 0x10‑0x14 | `F2_6`, `F1_1`–`F1_4`, and `F2_3` only. Empty/unused slots are omitted from the registry. | No CERN‑B variants. |
| **IT‑DTC** | Inner‑Tracker DTC board. All four 4-channel slots are populated. F1_1 is standard Tx-only; F1_2‑F1_4 and F2_2‑F2_4 contain separate CERN-B Tx and Rx devices. | R0A‑R1C at 0x10‑0x14 | `F1_5`, `F1_6`, `F2_5`, and `F2_6` plus the Tx/Rx devices. | `F1_2_Tx/Rx`, `F1_3_Tx/Rx`, `F1_4_Tx/Rx`, `F2_2_Tx/Rx`, `F2_3_Tx/Rx`, `F2_4_Tx/Rx` → CERN‑B variant. |

### Selecting a preset
```python
# Track‑Finder configuration
tf = Registry(setup='tf')
print(tf.fireflies['F1_2'].part_id)   # instance of standard FireflyRx
```

In a separate process, select the IT-DTC preset on the registry's first
construction:

```python
# Inner‑Tracker DTC configuration
it = Registry(setup='it_dtc')
print(it.fireflies['F1_2_Tx'].part_id)   # instance of FireflyTxCern
```
If `setup=None` (default), the Firefly layout is empty; clocks, power supplies,
the MCU, and FPGA objects are still populated. Use a named preset or call
`apply_preset()` before looking up Fireflies.

### Optional devices and hardware errors

`get_firefly()` raises `KeyError` when a location is not part of the selected
preset. Use `reg.fireflies.get(location)` when an empty or unused slot is
expected. Register operations raise `CMError` subclasses; their chained cause
may contain the MCU response, such as `Firefly not enabled`.

MCU register-map problems raise `McuProtocolError` subclasses (all `CMError`):
`McuMagicError` (System page lacks the `CMCU` magic), `McuMapVersionError`
(unsupported map major version) and `McuCoherencyError` (the Power snapshot
changed during every read attempt). **Breaking change:** these were previously
bare `RuntimeError`s, so a caller that caught `RuntimeError` for them must now
catch `CMError` (or `McuProtocolError`). `Registry.reset_all_lga80d_snapshots`
still raises `RuntimeError` for its `force=True` and power-state checks.

The worked `tools/examples.py` runner handles these expected conditions: missing
preset entries are reported as skips, per-device communication failures do not
stop iteration, and an error in one example does not prevent later examples
from running. It exits with status 1 if an entire example is stopped by an
expected hardware or configuration error.

## Extending the framework
1. **Add a new device** – create a subclass of `Device` in `device/`, implement `_encode_command`/`_decode_response` and any convenience properties.
2. **Populate the Registry** – extend `Registry._init` with the address mapping for the new device type, or add a new preset to `FIREfly_PRESETS` in `firefly_presets.py` (keyed by `BoardSetup`). Immutable clock/LGA80D wiring lives in `core_config.py`; the LGA80D "addresses" there are indices into the MCU firmware's `pm_addrs_dcdc[]` table (`address - 0x40`), not I2C addresses.
3. **Register definitions** – optional JSON/YAML files can be placed under `registers/` to document registers; they are not used by the code but serve as reference.

## Testing
The UART wrapper is lazy; in unit tests you can monkey‑patch `UART._ser` with a mock object that records writes and returns predefined bytes for reads.  This allows testing of device logic without hardware.

## License
MIT. There is no `LICENSE` file in this directory yet.
