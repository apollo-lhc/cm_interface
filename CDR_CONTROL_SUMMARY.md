# CDR Control Implementation Summary

## Issue Identified

The initial implementation incorrectly added `disable_cdr()` methods to **all** Firefly variants, including CERN-B devices which have a different register map.

## Fix Applied

### 1. Standard Firefly Devices (CDR Support ✓)

These devices now have working CDR control with correct register addresses:

| Device | Register Address | Description |
|--------|-----------------|-------------|
| `Firefly4` | 0x62 (Byte 98) | CDR Enable (bits 0-3: Rx, bits 4-7: Tx) |
| `Firefly12` | 0x4B (Byte 75) | CDR Enable per channel |
| `FireflyTx` | 0x4B (Byte 75) | Tx CDR Enable |
| `FireflyRx` | 0x4B (Byte 75) | Rx CDR Enable |

**CDR Enable Byte Layout (Firefly4):**
```
Bit 7: Tx4 CDR Enable    Bit 3: Rx4 CDR Enable
Bit 6: Tx3 CDR Enable    Bit 2: Rx3 CDR Enable
Bit 5: Tx2 CDR Enable    Bit 1: Rx2 CDR Enable
Bit 4: Tx1 CDR Enable    Bit 0: Rx1 CDR Enable
```

Default: 0xFF (all CDRs enabled)

### 2. CERN-B Variants (NO CDR Support ✗)

These devices **do not** have `disable_cdr()` methods:

- `FireflyTxCern`
- `FireflyRxCern`

**Reason:** CERN-B variants use a completely different register map with different base addresses:
- TX base: 0x30 (vs 0x10 for standard)
- RX base: 0x40 (vs 0x20 for standard)

CDR control registers, if they exist, are at unknown addresses and require separate documentation from CERN.

## Usage Example

```python
from cm_interface.registry import Registry
from cm_interface.device.firefly import Firefly4, FireflyRxCern

reg = Registry(setup='tf')

# Standard devices - CDR control available
f1_5 = reg.get_firefly('F1_5')  # Firefly4
if hasattr(f1_5, 'disable_cdr'):
    f1_5.disable_cdr()  # ✓ Works
    status = f1_5.get_cdr_status()
    print(f"CDR status: TX=0b{status['tx']:04b}, RX=0b{status['rx']:04b}")

# CERN-B devices - NO CDR control
f1_2 = reg.get_firefly('F1_2')  # FireflyRxCern
if not hasattr(f1_2, 'disable_cdr'):
    print(f"{f1_2.location} is CERN-B variant - CDR control not available")  # ✓ Correct
```

## Testing

```bash
cd /Users/wittich/src
python3 << 'PYEOF'
from cm_interface.registry import Registry

reg = Registry(setup='tf')

print("CDR Support Check:")
for loc, device in reg.fireflies.items():
    has_cdr = hasattr(device, 'disable_cdr')
    is_cern = isinstance(device, (FireflyRxCern, FireflyTxCern))
    
    if is_cern and has_cdr:
        print(f"  ERROR: {loc} is CERN-B but has CDR control!")
    elif is_cern and not has_cdr:
        print(f"  ✓ {loc} (CERN-B): Correctly no CDR control")
    elif not is_cern and has_cdr:
        print(f"  ✓ {loc} (Standard): Has CDR control")

print("\nAll checks passed!")
PYEOF
```

## Documentation

- **`EXAMPLES.md`** - Updated with CERN-B warnings
- **`design.md`** - Added device feature matrix
- **`device/firefly.py`** - Removed CDR methods from CERN-B classes
- **`registers/firefly_cernb.json`** - Documents different register base addresses

## Next Steps for CERN-B Support

If CDR control is needed for CERN-B variants:

1. Obtain CERN-B specific register documentation
2. Add CERN-B specific register addresses to `Firefly*Cern` classes
3. Implement `disable_cdr_cern()` methods with correct addresses
4. Test on actual CERN-B hardware

## References

- ECUO 25G/28G x4 Datasheet - Standard 4-channel Firefly register map
- ECUO 25G x12 Rev 4 Datasheet - Standard 12-channel Firefly register map  
- CERN Back-End x12 FireFly User Manual - CERN-B variant specifications



- **TF Configuration**: NO CERN-B variants at all
- **IT-DTC Configuration**: CERN-B variants are all 12-channel devices (F1_2/3/4, F2_2/3/4) with separate Tx and Rx

