# Design 

**Related Issue:** [apollo-lhc/cm_mcu#210](https://github.com/apollo-lhc/cm_mcu/issues/210) — UART device functionality for control of devices by SM/Zynq data path

## Overview

This repository implements a Python framework to manage access to devices 
accessible via UART (`/dev/ttySL4`). The UART is not accessible on the 
development machine — it exists on the target hardware (SM/Zynq platform).

## Architecture

The implementation follows a device abstraction pattern where each hardware 
component has a Python representation. Users access enumerated internal 
registers through these device objects.

### Device Types

| Device | Count | Type | UART Addresses |
|--------|-------|------|----------------|
| Si5395 Clock | 5 | R0A, R0B, R1A, R1B, R1C | 0x10–0x14 |
| Samtec Firefly | 20 | Tx/Rx split or 4-channel XCVR | 0x20–0x3F (page-based) |
| LGA80D DC/DC | 7 | Power supply monitors | 0x40–0x46 |

### Memory Map (per Issue #210)

| Page | Content |
|------|---------|
| 0 | Frequent baseline information |
| 1 | LGA80D devices |
| 2–22 | Firefly devices (one page per device) |
| 23–28 | Clock devices (Si5395) |

## Implementation Details

### Core Configuration (Immutable)

Clock and LGA80D UART addresses are fixed in `core_config.py`:

```python
clocks = {
    "R0A": 0x10, "R0B": 0x11,
    "R1A": 0x12, "R1B": 0x13, "R1C": 0x14,
}
lga80d = {
    "F1VCCINT1": 0x40, "F1VCCINT2": 0x41,
    "F2VCCINT1": 0x42, "F2VCCINT2": 0x43,
    "F1AVTT/CC": 0x44, "F2AVTT/CC": 0x45,
    "3V3/1V8":    0x46,
}
```

### Firefly Presets (Mutable)

Firefly layouts vary by board type. Presets are defined in `firefly_presets.py`:

- **TF (Track Finder):** 6 devices total
  - F1_1 to F1_4: Rx-only (standard ECUOR) - 4 devices
  - F2_3: Tx-only (standard ECUOT) - 1 device
  - F2_6: 4-channel XCVR (POPULATED) - 1 device
  - F1_5, F1_6, F2_5: 4-channel XCVR slots (EMPTY in hardware)
  - F2_1, F2_2, F2_4: UNUSED
  - **NO CERN-B variants**

- **IT-DTC:** 17 devices total
  - F1_1: Tx-only (standard) - 1 device
  - F1_2, F1_3, F1_4: CERN-B variants (both Tx and Rx) - 6 devices
  - F2_2, F2_3, F2_4: CERN-B variants (both Tx and Rx) - 6 devices
  - F1_5, F1_6, F2_5, F2_6: 4-channel XCVR - 4 devices
  - F2_1: UNUSED
  - **12 CERN-B devices total**

### Register Definitions

Complete JSON register maps are in `registers/`:

| File | Registers | Notes |
|------|-----------|-------|
| `si5395.json` | 100+ (page-based) | DSPLL config, I/O, NVM |
| `firefly12.json` | 24+ (lower + upper pages) | 12-channel Tx/Rx |
| `firefly4.json` | 22+ | 4-channel XCVR |
| `firefly_cernb.json` | 25+ | CERN-B variant (TX base 0x30, RX base 0x40) |
| `lga80d.json` | 44 PMBus commands | Voltage, current, protection |

## Board Configurations

The suggestion is to have representation of all the devices. There are 
five clocks, R0A, R0B, R1A, R1B, R1C. They are all Si5395. Data sheet in the
directory. There are 20 Samtec fireflies. They are available Using a naming
convention as below

```
% ff_dump_names
ff_dump_names: ID registers
      F1_1  12 Tx: --------------             F1_1  12 Rx: --------------
      F1_2  12 Tx: --------------             F1_2  12 Rx: --------------
      F1_3  12 Tx: --------------             F1_3  12 Rx: --------------
      F1_4  12 Tx: --------------             F1_4  12 Rx: --------------
      F1_5 4 XCVR: --------------
      F1_6 4 XCVR: --------------
      F2_1  12 Tx: --------------             F2_1  12 Rx: --------------
      F2_2  12 Tx: --------------             F2_2  12 Rx: --------------
      F2_3  12 Tx: --------------             F2_3  12 Rx: --------------
      F2_4  12 Tx: --------------             F2_4  12 Rx: --------------
      F2_5 4 XCVR: --------------
      F2_6 4 XCVR: --------------
```

There are also 7 LGA80D devices which can be named as follows
```
SUPPLY 3V3/1V8
VALUE 21.69     VALUE 22.19
SUPPLY F1VCCINT1
VALUE 21.78     VALUE 21.78
SUPPLY F1VCCINT2
VALUE 21.88     VALUE 21.88
SUPPLY F2VCCINT1
VALUE 20.53     VALUE 20.53
SUPPLY F2VCCINT2
VALUE 21.50     VALUE 22.00
SUPPLY F1AVTT/CC
VALUE 22.38     VALUE 21.88
SUPPLY F2AVTT/CC
VALUE 20.59     VALUE 20.59
```

In the directory there are data sheets for all the parts. 

I suggest a representation for each part, and then users access
that representation to get at an enumareated list of internal registers.

### IT-DTC Configuration

```
% ff_dump_names                                                                 
ff_dump_names: ID registers                                                     
       F1_1  12 Tx: ECUOT12251000513           F1_1  12 Rx: --------------       
       F1_2  12 Tx: CERNBY12024123M            F1_2  12 Rx: CRRNBY12024123M      
       F1_3  12 Tx: CERNBY12024123M            F1_3  12 Rx: CRRNBY12024123M      
       F1_4  12 Tx: CERNBY12024123M            F1_4  12 Rx: CRRNBY12024123M      
       F1_5 4 XCVR: OTP-2377687-01                                                
       F1_6 4 XCVR: OTP-237687-01                                                
       F2_1  12 Tx: --------------             F2_1  12 Rx: --------------       
       F2_2  12 Tx: CERNBY1201512701           F2_2  12 Rx: CRRNBY1201512701     
       F2_3  12 Tx: CERNBY1201512701           F2_3  12 Rx: CRRNBY1201512701     
       F2_4  12 Tx: CERNBY1201512701           F2_4  12 Rx: CRRNBY1201512701     
       F2_5 4 XCVR: OTP-237687-01                                                
       F2_6 4 XCVR: OTP-237687-01                                                
```

**Firefly Layout:**
- F1_1: Tx-only (12-ch)
- F1_2, F1_3, F1_4: CERN-B variants (12-ch, split Tx/Rx)
- F1_5, F1_6, F2_5, F2_6: 4-channel XCVR
- F2_1: Unused
- F2_2, F2_3, F2_4: CERN-B variants (12-ch, split Tx/Rx)

### TF (Track Finder) Configuration

```
% ff_dump_names
ff_dump_names: ID registers
       F1_1  12 Tx: --------------             F1_1  12 Rx: ECUOR12251000513
       F1_2  12 Tx: --------------             F1_2  12 Rx: ECUOR12251000513
       F1_3  12 Tx: --------------             F1_3  12 Rx: ECUOR12251000513
       F1_4  12 Tx: --------------             F1_4  12 Rx: ECUOR12251000513
       F1_5 4 XCVR: --------------
       F1_6 4 XCVR: --------------
       F2_1  12 Tx: --------------             F2_1  12 Rx: --------------
       F2_2  12 Tx: --------------             F2_2  12 Rx: --------------
       F2_3  12 Tx: ECUOT12251000513           F2_3  12 Rx: --------------
       F2_4  12 Tx: --------------             F2_4  12 Rx: --------------
       F2_5 4 XCVR: --------------
       F2_6 4 XCVR: OTP-237687-01
```

**Firefly Layout:**
- F1_1, F1_2, F1_3, F1_4: Rx-only (12-ch) - POPULATED
- F2_6: 4-channel XCVR - POPULATED
- F1_5, F1_6, F2_5: 4-channel XCVR slots - EMPTY (show "------" in ff_dump_names)
- F2_1, F2_2, F2_4: Unused (both Tx and Rx show "------")
- F2_3: Tx-only (12-ch) - POPULATED

**Total: 6 devices** (not 9 - three 4-ch XCVR slots are empty)

## Usage Example

```python
from cm_interface.registry import Registry
from cm_interface.device.si5395 import Si5395Reg

# Load TF configuration
reg = Registry(setup='tf')

# Access clock - Method 1: Symbolic register names (recommended)
clock = reg.get_clock('R0A')
status = clock.read_reg(Si5395Reg.LOL_HOLD_STATUS)  # Clear, self-documenting

# Access clock - Method 2: Convenience properties
is_locked = clock.is_locked()          # Check PLL lock status
mode = clock.mode                      # Get current mode
health = clock.health                  # Structured live clock status
device_id = clock.get_device_id()      # Should return 0x5395

# Access clock - Method 3: Status checks
if clock.has_loss_of_signal(0):        # Check LOS on IN0
    print("Input 0 has loss of signal")
if clock.is_in_holdover():
    print("Clock in holdover mode")

# Access firefly
ff = reg.get_firefly('F2_6')  # only populated 4-channel slot in TF
part_id = ff.part_id

# Access LGA80D
lga = reg.get_lga80d('F1VCCINT1')
voltage = lga.voltage
```

## Compliance with Issue #210

| Requirement | Implementation |
|-------------|----------------|
| UART communication | `uart.UART` class handles `/dev/ttySL4` |
| 8-bit addresses, 16-bit data | All device registers use this format |
| Page-based addressing | Si5395 (256 pages), Firefly (upper pages 0x00-0x0B) |
| Firefly support | `Firefly12`, `Firefly4`, `FireflyTx`, `FireflyRx`, CERN-B variants |
| LGA80D support | 44 PMBus commands documented in `lga80d.json` |
| Clock chip support | Si5395 register map with 100+ registers across 10 pages |
| High-priority interrupt handling | UART protocol supports command/response with CRC |
| Mutable board layouts | `firefly_presets.py` with TF, IT-DTC, custom configs |
| Immutable core wiring | `core_config.py` with frozen dataclass |

## Device Feature Matrix

| Device Type | CDR Control | LOS Monitoring | Temp/Voltage Monitoring | Notes |
|-------------|-------------|----------------|------------------------|-------|
| `Firefly4` (standard) | ✓ Yes | ✓ Yes | ✓ Yes | Full register support (F1_5, F1_6, F2_5, F2_6 in IT-DTC; F2_6 in TF) |
| `Firefly12` (standard) | ✓ Yes | ✓ Yes | ✓ Yes | Full register support |
| `FireflyTx` | ✓ Yes (Tx only) | ✗ N/A | ✓ Yes | Tx channels 1-12 (F2_3 in TF; F1_1 in IT-DTC) |
| `FireflyRx` | ✓ Yes (Rx only) | ✓ Yes | ✓ Yes | Rx channels 1-12 (F1_1 to F1_4 in TF) |
| `FireflyTxCern` | ✗ **No** | ✗ N/A | ✗ N/A | CERN-B variant, different register map (IT-DTC only) |
| `FireflyRxCern` | ✗ **No** | ✗ N/A | ✗ N/A | CERN-B variant, different register map (IT-DTC only) |
| `Si5395` (Clock) | ✗ N/A | ✓ LOS inputs | ✓ Temp/Voltage | PLL lock monitoring |
| `LGA80D` (DC-DC) | ✗ N/A | ✗ N/A | ✓ Voltage/Current | PMBus interface |

**Note:** 
- CERN-B variants (`FireflyTxCern`, `FireflyRxCern`) use different register addresses (TX base 0x30, RX base 0x40 vs standard 0x10/0x20).
- Do not attempt to use standard CDR control methods on CERN-B devices.
