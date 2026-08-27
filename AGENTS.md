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

- LGA80D `READ_FREQUENCY` values are reported in kHz (typical readings are
  457, 463, or 800 kHz).
- Unloaded LGA80D outputs have historically reported negative `READ_IOUT`
  values. Preserve the signed reading rather than clamping it to zero; use the
  PMBus status registers to determine whether the supply is reporting a fault.

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
firefly = reg.get_firefly('F1_5')   # Firefly transceiver
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
