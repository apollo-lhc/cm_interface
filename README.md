# cm_interface

Python framework for UART‑based management of Si5395 clocks, Samtec Firefly modules, and LGA80D devices.

## Overview
The library abstracts the low‑level UART serial protocol and provides a typed, object‑oriented view of each hardware block. Devices are discovered and instantiated through a singleton `Registry`.

## Package layout
```
cm_interface/
├─ __init__.py
├─ uart.py                # Lazy UART wrapper (pySerial) with debug logging
├─ errors.py              # Exception hierarchy
├─ utils.py               # CRC8, packing helpers
├─ registry.py            # Global Registry (clocks, fireflies, LGA80D)
├─ core_config.py         # Fixed UART addresses for clocks and LGA80D
├─ firefly_presets.py     # Board-specific Firefly layouts (TF, IT-DTC)
├─ device/
│  ├─ __init__.py
│  ├─ base.py             # Abstract Device class
│  ├─ si5395.py           # Clock implementation
│  ├─ firefly.py          # Tx, Rx, and 4‑channel firefly classes
│  └─ lga80d.py           # LGA80D DC-DC converter implementation
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
The package itself does not have external dependencies beyond `pyserial`.

## Basic usage
```python
from cm_interface.registry import Registry

# Default device (/dev/ttySL4)
reg = Registry()

# Custom device path (e.g., for testing or different hardware)
reg = Registry(dev_path="/dev/ttyUSB0")

# Clock example
clk = reg.get_clock('R0A')
print('Mode:', clk.mode)
print('Health:', clk.health)

# Firefly example – Tx/Rx pair (generic mapping)
# Note: keys follow the pattern "F{module}_{port}_Tx" / "_Rx" for split devices.
# Full‑duplex transceivers are accessed directly by location (e.g. "F1_5").

tx = reg.get_firefly('F1_2_Tx')
rx = reg.get_firefly('F1_2_Rx')
print('Tx part:', tx.part_id)
print('Rx part:', rx.part_id)

# 4‑channel transceiver (full‑duplex)
xf = reg.get_firefly('F1_5')
print('Transceiver part:', xf.part_id)

# LGA80D example
lga = reg.get_lga80d('F1VCCINT1')
print('Voltage:', lga.voltage, 'V')
print('Telemetry:', lga.read_telemetry(page=0))
```

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

## Standard board configurations
The repository ships two ready‑to‑use presets that match the real hardware layouts described in `design.md`.

| Config | Description | Clock address base | Firefly layout | Notable variants |
|-------|-------------|-------------------|---------------|------------------|
| **TF** | Track‑Finder board. Uses four 4‑channel transceivers (F1_5, F1_6, F2_5, F2_6). Rx‑only ports F1_1‑F1_4 are CERN‑B parts, and Tx‑only port F2_3 is a standard part. | R0A‑R1C at 0x10‑0x14 | 4‑channel transceivers at the XCVR locations; remaining ports are split into Tx/Rx objects. | `F1_2`, `F1_3`, `F1_4` → CERN‑B variant (different register map). |
| **IT‑DTC** | Inner‑Tracker DTC board. Same transceiver locations as TF. Tx‑only port F1_1 is standard; all other ports are dual (both Tx and Rx). Several ports use CERN‑B variants (F1_2, F1_3, F1_4, F2_2‑F2_4). | R0A‑R1C at 0x20‑0x24 | Same as TF for transceivers; dual ports are created as separate `*_Tx` and `*_Rx` objects. | `F1_2_Tx/Rx`, `F1_3_Tx/Rx`, `F1_4_Tx/Rx`, `F2_2_Tx/Rx`, `F2_3_Tx/Rx`, `F2_4_Tx/Rx` → CERN‑B variant. |

### Selecting a preset
```python
# Track‑Finder configuration
tf = Registry(setup='tf')
print(tf.fireflies['F1_2'].part_id)   # instance of FireflyRxCern

# Inner‑Tracker DTC configuration
it = Registry(setup='it_dtc')
print(it.fireflies['F1_2_Tx'].part_id)   # instance of FireflyTxCern
```
If `setup=None` (default) the registry falls back to the generic mapping used in the original implementation.

## Extending the framework
1. **Add a new device** – create a subclass of `Device` in `device/`, implement `_encode_command`/`_decode_response` and any convenience properties.
2. **Populate the Registry** – extend `Registry._populate` with the address mapping for the new device type or add a new preset to `config.PRESET_CONFIGS`.
3. **Register definitions** – optional JSON/YAML files can be placed under `registers/` to document registers; they are not used by the code but serve as reference.

## Testing
The UART wrapper is lazy; in unit tests you can monkey‑patch `UART._ser` with a mock object that records writes and returns predefined bytes for reads.  This allows testing of device logic without hardware.

## License
MIT – see `LICENSE` file (if added).
```python
from cm_interface.registry import Registry

reg = Registry()

# Clock example
clk = reg.get_clock('R0A')
print('Mode:', clk.mode)
print('Health:', clk.health)

# Firefly example – Tx/Rx pair
tx = reg.get_firefly('F1_2_Tx')
rx = reg.get_firefly('F1_2_Rx')
print('Tx part:', tx.part_id)
print('Rx part:', rx.part_id)

# 4‑channel transceiver (full‑duplex)
xf = reg.get_firefly('F1_5')
print('Transceiver part:', xf.part_id)

# LGA80D example
lga = reg.get_lga80d('F1VCCINT1')
print('Voltage:', lga.voltage, 'V')
print('Telemetry:', lga.read_telemetry(page=0))
```
All devices expose `read_reg`/`write_reg` for raw register access and higher‑level properties where implemented (e.g., `Clock.mode`, `LGA80D.voltage`).

## Extending the framework
1. **Add a new device** – create a subclass of `Device` in `device/`, implement `_encode_command`/`_decode_response` and any convenience properties.
2. **Populate the Registry** – extend `Registry._populate` with the address mapping for the new device type.
3. **Register definitions** – optional JSON/YAML files can be placed under `registers/` to document registers; they are not used by the code but serve as reference.

## Testing
The UART wrapper is lazy; in unit tests you can monkey‑patch `UART._ser` with a mock object that records writes and returns predefined bytes for reads.  This allows testing of device logic without hardware.

## License
MIT – see `LICENSE` file (if added).
