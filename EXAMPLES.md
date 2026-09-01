# Worked Examples for cm_interface

This document provides comprehensive, ready-to-use examples for controlling Si5395 clocks, Firefly optical transceivers, and LGA80D DC-DC converters.

## Table of Contents

1. [Quick Start](#quick-start)
2. [UART Debug Logging](#uart-debug-logging)
3. [Firefly CDR Control](#firefly-cdr-control)
4. [Clock Monitoring](#clock-monitoring)
5. [LGA80D Power Management](#lga80d-power-management)
6. [Register Access Patterns](#register-access-patterns)
7. [Complete System Status](#complete-system-status)

---

## Quick Start

```python
from cm_interface.registry import Registry

# Initialize with TF (Track Finder) configuration
reg = Registry(setup='tf')

# Access devices
clock = reg.get_clock('R0A')
firefly = reg.get_firefly('F2_6')  # the populated TF 4-channel slot
lga = reg.get_lga80d('F1VCCINT1')
```

---

## UART Debug Logging

Enable debug logging to see all UART transactions in real-time. Useful for troubleshooting and understanding register access patterns.

### Example: Debug to stdout

```python
import sys
from cm_interface.registry import Registry

# Enable debug output to stdout
Registry._instance = None  # Reset singleton if needed
reg = Registry(setup='tf', debug=sys.stdout)

clock = reg.get_clock('R0A')
device_id = clock.get_device_id()
print(f"Device ID: 0x{device_id:04X}")
```

**Output:**
```
UART TX (7): 10 00 00 02 00 7b
UART RX (4): 10 00 02 95
UART TX (7): 10 00 00 03 00 7a
UART RX (4): 10 00 03 53
Device ID: 0x5395
```

### Example: Debug to file

```python
from cm_interface.registry import Registry

# Log all UART traffic to file
with open('uart_debug.log', 'w') as log_file:
    Registry._instance = None
    reg = Registry(setup='tf', debug=log_file)
    
    clock = reg.get_clock('R0A')
    
    # Read DESIGN_ID from upper page (0x026B-0x0272 on page 0x02)
    design_id = b''
    for offset in range(8):
        design_id += clock.read_reg(0x026B + offset)
    
    design_id_str = design_id.rstrip(b'\x00').decode('ascii')
    print(f"DESIGN_ID: {design_id_str}")
```

### Example: Debug to stderr

```python
import sys
from cm_interface.registry import Registry

# Debug to stderr (useful when stdout is used for other output)
Registry._instance = None
reg = Registry(setup='tf', debug=sys.stderr)
```

### Understanding UART Debug Output

Each transaction shows:
```
UART TX (7): 10 00 02 6b 00 79
│          │  │  │  │    │  └── CRC8 checksum
│          │  │  │    │  └───── Payload length (0 = read)
│          │  │  └─────────── Register address (0x026B)
│          │  └────────────── Write flag (0x00 = READ, 0x01 = WRITE)
│          └───────────────── Device address (0x10 = R0A)
└──────────────────────────── Direction (TX = transmit)
```

**Note:** Debug logging is disabled by default (no performance impact).

---

## Firefly CDR Control

**Important:** CERN-B variant devices (`FireflyRxCern`, `FireflyTxCern`) have a different register map and **do not support** CDR control through the standard addresses. The `disable_cdr()` method is only available on standard Firefly devices.


### Example 1: Disable CDR on All Firefly Devices

```python
from cm_interface.registry import Registry
from cm_interface.device.firefly import Firefly4, Firefly12, FireflyTx, FireflyRx
from cm_interface.errors import CMError

reg = Registry(setup='tf')

# Iterate over all firefly devices
for loc, device in reg.fireflies.items():
    # Skip CERN-B variants (they don't support standard CDR control)
    if not hasattr(device, 'disable_cdr'):
        print(f"Skipping {loc} (CERN-B variant - no CDR control)")
        continue
    
    print(f"Disabling CDR on {loc}...")
    
    try:
        # Disable CDR on all channels and print status
        device.disable_cdr()
        device.print_status()
    except (CMError, OSError, TimeoutError, ValueError) as exc:
        cause = exc.__cause__ or exc.__context__
        detail = f" (caused by: {cause})" if cause else ""
        print(f"Skipping {loc}: {exc}{detail}")
```

**Output:**
```
Disabling CDR on F2_6...
Skipping F2_6: Read register 0x62 failed (caused by: MCU error: Firefly not enabled)
```

### Example 2: Selective CDR Control

```python
# F2_6 is the only populated 4-channel transceiver in the TF preset.
f2_6 = reg.fireflies.get('F2_6')
if f2_6 is None:
    print("F2_6 is not configured")
else:
    # Disable CDR only on specific channels.
    f2_6.disable_cdr(channels=[1, 2])

    # Disable CDR only on the Tx side of a full-duplex device.
    f2_6.disable_cdr(tx=True, rx=False)

    # Firefly4 reports aggregate four-bit Tx and Rx masks.
    status = f2_6.get_cdr_status()
    print(f"CDR masks: TX=0b{status['tx']:04b}, RX=0b{status['rx']:04b}")
```

`F1_5`, `F1_6`, and `F2_5` are empty in the TF layout and are not registry
keys. Use `reg.fireflies.get(location)` for optional slots. `F1_1` is Rx-only
in TF, so it must not be passed the `tx=`/`rx=` arguments supported by
full-duplex `Firefly4` and `Firefly12` devices.

### Example 3: CDR Control by Device Type

```python
from cm_interface.device.firefly import Firefly4, Firefly12, FireflyTx, FireflyRx

# Handle different device types differently
for loc, device in reg.fireflies.items():
    if isinstance(device, Firefly4):
        print(f"{loc}: 4-channel XCVR - disabling all 4 channels")
        device.disable_cdr(channels=[1, 2, 3, 4])
    elif isinstance(device, Firefly12):
        print(f"{loc}: 12-channel - disabling all channels")
        device.disable_cdr()
    elif isinstance(device, FireflyTx):
        print(f"{loc}: Tx-only - disabling Tx CDR")
        device.disable_cdr()
```

---

## Clock Monitoring

### Example 4: Check PLL Lock Status

```python
from cm_interface.device.si5395 import Si5395Reg

reg = Registry(setup='tf')

# Check all clocks
for name, clock in reg.clocks.items():
    print(f"\nClock {name}:")
    
    # Using convenience methods (RECOMMENDED)
    if clock.is_locked():
        print(f"  ✓ PLL is locked")
    else:
        print(f"  ✗ PLL is NOT locked")
    
    if clock.is_in_holdover():
        print(f"  ⚠ In holdover mode")
    
    # Check for loss of signal on each input
    for i in range(4):
        if clock.has_loss_of_signal(i):
            print(f"  ⚠ LOS on Input {i}")
    
    # Get device information
    print(f"  Device ID: 0x{clock.get_device_id():04X}")
    print(f"  Revision: {clock.get_device_rev()}")
```

### Example 5: Read Clock Status Registers

```python
# Method 1: Symbolic register names (RECOMMENDED)
status = clock.read_reg(Si5395Reg.LOL_HOLD_STATUS)
mode = clock.mode

# Method 2: Convenience properties
is_locked = clock.is_locked()
current_mode = clock.mode

# Method 3: Raw hex addresses (legacy)
status = clock.read_reg(0x000E)  # LOL_HOLD_STATUS address
```

### Example 5b: Reading Upper-Page Registers (DESIGN_ID)

The Si5395 has registers on multiple pages. The UART bridge handles page switching automatically when you use a 16-bit address.

```python
import sys
from cm_interface.registry import Registry

reg = Registry(setup='tf', debug=sys.stdout)  # Enable debug to see transactions
clock = reg.get_clock('R0A')

# Read 8-byte DESIGN_ID from page 0x02 (registers 0x026B-0x0272)
# No manual page switching needed!
design_id = b''
for offset in range(8):
    design_id += clock.read_reg(0x026B + offset)

design_id_str = design_id.rstrip(b'\x00').decode('ascii', errors='ignore')
print(f"DESIGN_ID: {design_id_str}")
```

**Expected output (with debug enabled):**
```
UART TX (7): 10 00 02 6b 00 79
UART RX (4): 10 02 6b 55 c7
UART TX (7): 10 00 02 6c 00 7a
UART RX (4): 10 02 6c 4c d8
...
DESIGN_ID: ULT.1A
```

**Note:** The UART bridge automatically handles page selection when you provide the full 16-bit address (e.g., `0x026B` = page `0x02`, offset `0x6B`). You do **not** need to manually write to the PAGE register.

### Example 6: Change Clock Mode

```python
# Set clock to locked mode
health = clock.health

# Perform soft reset
clock.reset(soft=True)

# Select input clock 0
clock.set_input_select(0)

# Enable hitless switching
clock.enable_hitless_switching(True)
```

---

## LGA80D Power Management

### Example 7: Monitor All Power Supplies

```python
from cm_interface.errors import CMError

reg = Registry(setup='tf')

print("LGA80D Power Supply Status:\n")
for name, lga in reg.lga80d.items():
    try:
        voltage = lga.voltage
        status = lga.read_status()
        state = "FAULT" if status.has_faults else "OK"
        print(
            f"{name:12s}: {voltage:6.2f} V, {state}, "
            f"STATUS_WORD=0x{status.word:04X}"
        )
    except (CMError, OSError, TimeoutError, ValueError) as exc:
        print(f"{name:12s}: ERROR - {exc}")
```

**Output:**
```
LGA80D Power Supply Status:

3V3/1V8     :   1.80 V, OK, STATUS_WORD=0x0000
```

---

## Register Access Patterns

### Comparison of Three Access Methods

| Method | Example | Pros | Cons |
|--------|---------|------|------|
| **Symbolic Names** | `clock.read_reg(Si5395Reg.LOL_HOLD_STATUS)` | Self-documenting, IDE autocomplete, type-safe | Requires enum import |
| **Convenience Properties** | `clock.is_locked()` | Most readable, encapsulates logic | Limited to common operations |
| **Raw Hex** | `clock.read_reg(0x000E)` | Direct datasheet reference, no imports needed | Error-prone, not self-documenting |

### Best Practices

```python
# ✓ RECOMMENDED: Symbolic names
from cm_interface.device.si5395 import Si5395Reg
status = clock.read_reg(Si5395Reg.LOL_HOLD_STATUS)

# ✓ RECOMMENDED: Convenience methods
if clock.is_locked():
    print("Locked")

# ⚠ ACCEPTABLE: Raw hex for one-off debugging
status = clock.read_reg(0x000E)

# ✗ AVOID: Magic numbers in production code
if clock.read_reg(14)[0] & 0x02:  # What does 14 mean? What does 0x02 check?
    ...
```

---

## Complete System Status

### Example 8: Full System Report

```python
from cm_interface.errors import CMError


def generate_system_report(reg):
    """Generate comprehensive status report for all devices."""
    
    print("=" * 70)
    print("SYSTEM STATUS REPORT")
    print("=" * 70)
    
    # Clock summary
    print("\n--- Clocks ---")
    for name, clock in reg.clocks.items():
        locked = "LOCKED" if clock.is_locked() else "UNLOCKED"
        print(f"  {name}: {locked}, ID=0x{clock.get_device_id():04X}")
    
    # Firefly summary
    print("\n--- Fireflies ---")
    for loc, device in reg.fireflies.items():
        dev_type = type(device).__name__
        part_id = device.part_id
        print(f"  {loc:10s} ({dev_type:12s}): part_id={part_id}")
    
    # LGA80D summary
    print("\n--- LGA80D DC-DC Converters ---")
    for name, lga in reg.lga80d.items():
        try:
            voltage = lga.voltage
            print(f"  {name:12s}: {voltage:.2f} V")
        except (CMError, OSError, TimeoutError, ValueError) as exc:
            print(f"  {name:12s}: ERROR - {exc}")
    
    print("\n" + "=" * 70)

# Usage
reg = Registry(setup='tf')
generate_system_report(reg)
```

### Example 9: Device Inventory by Type

```python
from cm_interface.device.firefly import Firefly4, Firefly12, FireflyTx, FireflyRx

reg = Registry(setup='tf')

# Categorize fireflies by type
inventory = {
    '4ch_xcvr': [],
    '12ch_xcvr': [],
    'tx_only': [],
    'rx_only': [],
}

for loc, device in reg.fireflies.items():
    if isinstance(device, Firefly4):
        inventory['4ch_xcvr'].append(loc)
    elif isinstance(device, Firefly12):
        inventory['12ch_xcvr'].append(loc)
    elif isinstance(device, FireflyTx):
        inventory['tx_only'].append(loc)
    elif isinstance(device, FireflyRx):
        inventory['rx_only'].append(loc)

# Print inventory
print("Firefly Device Inventory:")
print(f"  4-channel XCVRs: {len(inventory['4ch_xcvr'])} - {inventory['4ch_xcvr']}")
print(f"  12-channel XCVRs: {len(inventory['12ch_xcvr'])} - {inventory['12ch_xcvr']}")
print(f"  Tx-only: {len(inventory['tx_only'])} - {inventory['tx_only']}")
print(f"  Rx-only: {len(inventory['rx_only'])} - {inventory['rx_only']}")
```

---

## Running the Examples

The complete examples are available in `examples.py`. Run them with:

```bash
python3 examples.py
```

Or import individual functions:

```python
from cm_interface.examples import example_disable_cdr_all_fireflies
example_disable_cdr_all_fireflies()
```

---

## API Reference

### Registry

| Method | Description |
|--------|-------------|
| `Registry(setup='tf')` | Create registry with board preset ('tf', 'it_dtc', or None) |
| `get_clock(name)` | Get Si5395 clock by name (e.g., 'R0A') |
| `get_firefly(loc)` | Get a configured Firefly by location; raises `KeyError` when absent |
| `get_lga80d(name)` | Get LGA80D converter by supply name |

### Firefly Devices

| Method | Description |
|--------|-------------|
| `disable_cdr(channels, tx, rx)` | Disable CDR on specified channels/sides |
| `get_cdr_status(channel, tx, rx)` | Get CDR enable status |
| `print_status()` | Print device status to console |
| `part_id` | Module part-identification string |
| `vendor_name` | Vendor-name string |
| `revision_number` | Vendor revision string when defined for the variant |
| `serial_number` | Module serial-number string, or `None` when unsupported |
| `temperature` | Module temperature in degrees Celsius |
| `supply_voltage` | Module supply voltage in volts |

### Clock Devices

| Method | Description |
|--------|-------------|
| `is_locked()` | Check if PLL is locked |
| `is_in_holdover()` | Check if in holdover mode |
| `has_loss_of_signal(input_num)` | Check LOS on input |
| `get_device_id()` | Read device ID register |
| `get_device_rev()` | Read revision |
| `reset(soft)` | Perform reset |
| `mode` | Property: get/set clock mode |

### LGA80D Devices

| Method | Description |
|--------|-------------|
| `voltage` | Page-0 output-voltage property |
| `read_telemetry(page)` | Read page-aware VIN, VOUT, IOUT, temperature, frequency (kHz), and status |
| `read_status(page)` | Return `LGA80DStatus` with `has_faults` and raw status fields |
| `read_output_current(page)` | Read output current |

LGA80D output-current readings are intentionally not clamped. Unloaded outputs
on the current hardware have historically reported negative values; check the
PMBus status registers when deciding whether a reading represents a fault.
`read_status()` is structured rather than integer-valued: format
`status.word` with `04X`, the category fields with `02X`, and use
`status.has_faults` for an overall `OK`/`FAULT` indication.

---

## See Also

- `AGENTS.md` - How to integrate with Opencode agents
- `design.md` - Architecture and hardware configuration details
- `registers/*.json` - Complete register definitions for all devices
