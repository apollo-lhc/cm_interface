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
├─ utils.py               # CRC8, packing helpers
├─ registry.py            # Global Registry for board devices
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
`reg.get_mcu()`. The current Python client implements the read-only Phase-1
interface for ProgCom device `MC 0`:

```python
mcu = reg.get_mcu()
info = mcu.system_info
print(info.git_version, info.uptime_seconds, info.capabilities, info.health)

adc = mcu.read_adc()
print(adc["VCC_12V"].value, adc["VCC_12V"].valid)

targets = mcu.read_adc_targets()
print(targets["F1_VCCINT"].value, targets["F1_VCCINT"].valid)
```

`system_info` validates the `CMCU` magic and map major version 1. ADC and
target snapshots contain 21 little-endian IEEE-754 binary16 values and use
generation counters to avoid returning a torn multi-transaction snapshot.
ADC readings include validity and error bitmaps; targets include a validity
bitmap. Snapshot entries can be selected by channel name.

Power, alarm, persistent-log, and control pages are reserved in the enums but
do not yet have Python accessors. The Python implementation is unit-tested with
an in-memory endpoint; this repository does not contain the matching MCU
firmware or establish what is installed on a target. The deployed firmware
must implement the version-1 `MC 0` map. Older firmware may return
`MCU device not implemented`. The remaining firmware and Python phases are
tracked in `../MCU_FIRMWARE_IMPLEMENTATION_PLAN.md` and
`../MCU_DEVICE_PLAN.md`.

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
loaded FPGA bitfile must instantiate the generic I2C slave. Bitfile profiles,
identity matching, named registers, permissions, and high-level diagnostics
are not implemented yet; raw writes therefore have no semantic safety checks.
Those later phases are described in `../FPGA_GENERIC_INTERFACE_PLAN.md`.

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
the raw PMBus status registers; `has_faults` is true if any are nonzero.

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

The worked `examples.py` runner handles these expected conditions: missing
preset entries are reported as skips, per-device communication failures do not
stop iteration, and an error in one example does not prevent later examples
from running. It exits with status 1 if an entire example is stopped by an
expected hardware or configuration error.

## Extending the framework
1. **Add a new device** – create a subclass of `Device` in `device/`, implement `_encode_command`/`_decode_response` and any convenience properties.
2. **Populate the Registry** – extend `Registry._populate` with the address mapping for the new device type or add a new preset to `config.PRESET_CONFIGS`.
3. **Register definitions** – optional JSON/YAML files can be placed under `registers/` to document registers; they are not used by the code but serve as reference.

## Testing
The UART wrapper is lazy; in unit tests you can monkey‑patch `UART._ser` with a mock object that records writes and returns predefined bytes for reads.  This allows testing of device logic without hardware.

## License
MIT – see `LICENSE` file (if added).
