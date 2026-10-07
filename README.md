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
├─ examples.py            # Worked examples (see EXAMPLES.md)
├─ exercise_mcu.py        # Exercise the MCU pages; writes/control prompt unless --yes
├─ lga80d_dump.py         # LGA80D register dump script (--snapshot adds the SNAPSHOT read)
├─ MCU_REGISTER_MAP.md    # MCU wire contract (with MCU_Reg.h)
├─ TODO.md                # Open, deferred and dropped cm_interface work
├─ FUTURE_IDEAS.md        # Ideas not yet planned
├─ lga80d_status.py       # Read-only decoded LGA80D status registers (one device or --all)
├─ core_config.py         # Fixed UART addresses for clocks and LGA80D
├─ firefly_presets.py     # Board-specific Firefly layouts (TF, IT-DTC)
├─ device/
│  ├─ __init__.py
│  ├─ base.py             # Abstract Device class
│  ├─ si5395.py           # Clock implementation
│  ├─ firefly.py          # Tx, Rx, and 4‑channel firefly classes
│  ├─ lga80d.py           # LGA80D DC-DC converter implementation
│  ├─ mcu.py              # Command-module MCU register interface
│  └─ fpga.py             # Raw F1/F2 generic register interface
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
`print(snapshot)` (or `snapshot.format_lines()`) gives a readable summary; `lga80d_dump.py --snapshot`
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

The worked `examples.py` runner handles these expected conditions: missing
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
