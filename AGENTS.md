# AGENTS.md

This repository does **not** define any custom Opencode agents. The framework is a pure Python library that can be leveraged by external agents or scripts.

## How agents can interact

- **General agents** (e.g., `explore` or `general`) can import `cm_interface` to query hardware state, generate register reports, or drive configuration workflows.
- **Specialized agents** (e.g., a build‑automation or CI agent) may use the `Registry` to verify that all expected devices are reachable before proceeding with a test run.
- Because the UART connection is opened lazily, an agent can safely instantiate the `Registry` on a development host where `/dev/ttySL4` does not exist; the actual serial port will only be accessed when a read/write is performed on a device.
- **Debug logging** is available for troubleshooting: pass `debug=sys.stdout` or `debug=file_object` to `Registry()` to log all UART transactions.

## Architecture

The register configuration is split into:

- **Immutable core wiring** (`core_config.py`) — Fixed UART addresses for Si5395 clocks and LGA80D DC-DC converters
- **Mutable firefly presets** (`firefly_presets.py`) — Board-specific Firefly optical transceiver layouts (TF, IT-DTC, custom)
- **Register definitions** (`registers/*.json`) — Comprehensive JSON maps for all devices with addresses, sizes, and descriptions

## Known hardware telemetry behavior

- Target Zynq systems may run Python 3.6. Keep package source compatible with
  Python 3.6 syntax and APIs. Import `dataclass` and space-separated hexadecimal
  formatting through `compat.py`; do not require the external `dataclasses`
  backport, `typing.Final`, built-in generic annotations such as `tuple[int]`,
  or the Python 3.8 `bytes.hex(sep)` form.
- The TF Firefly preset contains only populated devices: standard Rx-only
  modules at `F1_1` through `F1_4`, a standard Tx-only module at `F2_3`, and a
  standard 4-channel transceiver at `F2_6`. `F1_5`, `F1_6`, and `F2_5` are
  empty and are intentionally absent. TF has no CERN-B variants.
- `Registry.get_firefly()` raises `KeyError` for a location absent from the
  selected preset. Inventory or optional-slot code should use
  `registry.fireflies.get(location)` and report a skip when it returns `None`.
- LGA80D `READ_FREQUENCY` values are reported in kHz (typical readings are
  457, 463, or 800 kHz).
- Unloaded LGA80D outputs have historically reported negative `READ_IOUT`
  values. Preserve the signed reading rather than clamping it to zero; use the
  PMBus status registers to determine whether the supply is reporting a fault.
- `LGA80D.read_status()` returns the structured `LGA80DStatus` dataclass, not
  a scalar. Use `status.has_faults` for the summary and format `status.word` as
  a 16-bit value; the category fields are 8-bit values.
- Worked hardware examples should catch expected `CMError`, OS/UART timeout,
  and setup lookup failures at an appropriate device/example boundary. Include
  chained exception text so an MCU reason such as `Firefly not enabled` is not
  hidden, continue independent operations, and do not broadly suppress
  unexpected programming errors such as `TypeError`.

## Planned device extensions

Two device families are planned but are not yet implemented in this Python
package or the MCU ProgCom protocol:

- The MCU itself, for power-state and alarm status, ADC telemetry, reset-cause
  and failure diagnostics, and controlled clearing of latched errors. The
  existing Zynq push-monitoring data does not need to be duplicated blindly;
  pull registers should concentrate on live diagnostics, detailed failure
  cause, and state needed for debugging. Power commands must define arbitration
  with the dedicated Zynq power-control GPIO and the existing CLI. Preserve
  both error histories: the expressive volatile CLI log and the compact
  persistent EEPROM log. ADC values use the same IEEE-754 binary16 bit pattern
  as `ZynqMonTask`, transported as two little-endian ProgCom bytes. Raw ADC
  counts are not exposed. The first release also exposes the configured
  voltage-alarm targets in binary16, with a target-valid channel bitmap. See
  `../MCU_DEVICE_PLAN.md`.

  MCU clear operations follow the existing CLI semantics. An alarm normally
  cuts board power, removing its triggering condition before the operator
  clears the retained latch; power `clearfail` clears the active power-failure
  latch immediately. ProgCom confirmation means the request was queued, and
  completion is read from the normal latch/FSM status rather than a generic
  command tracker. Asserting the ProgCom inhibit first is recommended but is
  not a clear-command precondition. Clearing must retain last-failure and
  persistent history.

  The persistent EEPROM error ring is only 64 words. Expose its capacity and a
  mutation generation, but do not add a maintained valid-entry count; Python
  can scan the complete logical ring and ignore empty/erased sentinels.

  The MCU device and its ProgCom register map target CM REV2 and REV3 only.
  REV1 is out of scope; do not design an alternate transport or REV1-specific
  map.
- Two FPGA generic I2C endpoints, one on F1 and one on F2. These require a
  stable raw transport plus data-driven, bitfile-specific Python profiles;
  their register maps must not be compiled into the MCU. Profiles are selected
  independently because F1 and F2 may load different designs. See
  `../FPGA_GENERIC_INTERFACE_PLAN.md`.

### FPGA generic-port hardware facts

The CM REV3 schematic, sheet 38, and the current `clk_freq_fpga_cmd` establish:

- the ports share MCU I2C5 and TCA9548A U46 (`0x70`) with FPGA SYSMON;
- F2 generic is mux channel 0, F2 SYSMON channel 1, F1 generic channel 2,
  and F1 SYSMON channel 3;
- U46 is also the 3.3-V/1.8-V level boundary, with downstream pull-ups to
  1.8 V;
- generic modules are instantiated by the bitfile and are unavailable before
  FPGA configuration, while SYSMON remains available whenever FPGA power is
  present;
- the example frequency-counter endpoint uses slave `0x2b`, one-byte register
  addresses, and 32-bit words at four-byte strides, but `0x2b` is an FPGA
  design convention rather than a board-strapped address;
- FPGA pin B29 is strapped high on F1 and low on F2, so a common bitfile can
  expose or validate its physical position; and
- `/I2C_RESET_FPGAS` resets the shared mux, so generic-port error recovery
  must not toggle it without coordinating with SYSMON monitoring.

The proposed Python design has a raw `FPGA` object for F1/F2 and validated
JSON profiles for named registers, decoding, access permissions, and optional
high-level diagnostics. Legacy bitfiles require explicit profile selection.
Future bitfiles should expose a small read-only descriptor containing magic,
interface ABI, profile/design ID, register-map hash, build ID, capabilities,
and optionally the F1/F2 position. Do not guess a bitfile by reading arbitrary
registers.

The concrete project
`/nfs/cms/hw/wittich/25G_2/vu13p_ibert_25g` shows how complex instances fit
this design. Its `mcu/gt_test_mcu_protocol.h` is a bitfile-owned portable wire
protocol, and `mcu/cm_mcu/GTTestEngine.c/.h` are an optional high-level MCU
adapter for commit-last requests and coherent result collection. A matching
Python profile should reuse those semantics. The adapter should sit above the
common generic-FPGA routing helper rather than duplicate mux/semaphore code.
The design occupies `0xa0`-`0xff`, so that range cannot be reserved for a
universal identity descriptor; select a non-conflicting common-header or
banking convention only after comparing real designs.

Generic transactions must acquire the same I2C5 semaphore as SYSMON, protect
mux selection and cleanup, and release it on every path. Raw operations should
normally contain one short transfer. A bitfile-specific MCU service may group
a bounded set of transfers for commit/coherency, but must never hold I2C5
across sleeps, FPGA test execution, or frequency-counter accumulation. A
missing generic endpoint is an expected bitfile condition, not automatically
a board fault.

## Registry Usage

```python
from cm_interface.registry import Registry

# Default (empty firefly layout, core config from core_config.py)
reg = Registry()

# Load a board preset ('tf' or 'it_dtc')
reg = Registry(setup='tf')

# With debug logging enabled
import sys
reg = Registry(setup='tf', debug=sys.stdout)

# Access devices
clock = reg.get_clock('R0A')        # Si5395 clock generator
firefly = reg.get_firefly('F2_6')   # populated TF Firefly4 transceiver
lga80d = reg.get_lga80d('F1VCCINT1') # LGA80D DC-DC converter
```

## Register Access

All devices expose `read_reg()` and `write_reg()` methods that accept 16-bit addresses. The UART bridge automatically handles page selection:

```python
# Read from page 0x00 (default page)
status = clock.read_reg(0x000E)  # LOL_HOLD_STATUS

# Read from page 0x02 (DESIGN_ID) - no manual page switch needed!
design_id = b''
for offset in range(8):
    design_id += clock.read_reg(0x026B + offset)

# Symbolic register names (recommended)
from cm_interface.device.si5395 import Si5395Reg
device_id = clock.read_reg(Si5395Reg.DEVICE_ID)
```

**Important:** Do not manually write to the PAGE register (0x0001). The UART bridge encodes the full 16-bit address in each command, automatically selecting the correct page.

## Extending with agents

If you need an Opencode agent that performs higher‑level tasks (e.g., bulk configuration, health‑check scripts, or integration with a CI pipeline):

```python
from cm_interface.registry import Registry

class MyAgent:
    def run(self):
        reg = Registry(setup='tf')  # or 'it_dtc', or None for custom
        # perform actions, return JSON or human‑readable report
```

## Register Documentation

Complete register maps are available in the `registers/` directory:

| File | Device | Registers |
|------|--------|-----------|
| `si5395.json` | Si5395 Clock Generator | 100+ (page-based) |
| `firefly12.json` | Firefly 12-channel | 24+ (lower + upper pages) |
| `firefly4.json` | Firefly 4-channel | 22+ |
| `firefly_cernb.json` | Firefly CERN-B variant | 25+ |
| `lga80d.json` | LGA80D DC-DC Converter | 44 PMBus commands |

No additional configuration files are required beyond the standard Opencode agent manifest.
