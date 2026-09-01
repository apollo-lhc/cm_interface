#!/usr/bin/env python3
"""
Worked examples for cm_interface hardware control.

This module demonstrates common operations for managing Si5395 clocks,
Firefly optical transceivers, and LGA80D DC-DC converters.
"""

from cm_interface.registry import Registry
from cm_interface.device.si5395 import Si5395Reg
from cm_interface.device.firefly import Firefly12, Firefly4, FireflyTx, FireflyRx
from cm_interface.errors import CMError


EXPECTED_HARDWARE_ERRORS = (CMError, OSError, TimeoutError, ValueError)
EXPECTED_EXAMPLE_ERRORS = EXPECTED_HARDWARE_ERRORS + (KeyError,)


def _format_error(exc):
    """Return a compact message that includes useful chained exceptions."""
    messages = []
    seen = set()
    current = exc

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip() or type(current).__name__
        if message not in messages:
            messages.append(message)
        current = current.__cause__ or current.__context__

    return " (caused by: ".join(messages) + ")" * (len(messages) - 1)


def _report_error(context, exc):
    """Print an expected hardware error without producing a traceback."""
    print(f"  ⚠ {context}: {_format_error(exc)}")


def _get_configured_firefly(reg, location):
    """Return a configured Firefly, or report that the slot is unavailable."""
    device = reg.fireflies.get(location)
    if device is None:
        print(f"  ⚠ Skipping {location}: not present in the selected board setup")
    return device


# ============================================================================
# Example 1: Disable CDR on All Firefly Devices
# ============================================================================

def example_disable_cdr_all_fireflies():
    """
    Iterate over all firefly devices and disable CDR (Clock Data Recovery)
    on all channels, then print the status.
    """
    print("=" * 70)
    print("Example 1: Disable CDR on All Firefly Devices")
    print("=" * 70)
    
    # Initialize registry with TF (Track Finder) configuration
    reg = Registry(setup='tf', dev_path='/dev/ttyUSB2')
    
    # Iterate over all firefly devices
    for loc, device in reg.fireflies.items():
        print(f"\nProcessing {loc}...")
        
        # Check if device has CDR control (all firefly variants do)
        if hasattr(device, 'disable_cdr'):
            try:
                # Disable CDR on all channels
                device.disable_cdr()
                print(f"  ✓ CDR disabled on all channels")

                # Print device status
                device.print_status()
            except EXPECTED_HARDWARE_ERRORS as exc:
                _report_error(f"Skipping {loc}", exc)
        else:
            print(f"  ⚠ Device {loc} does not support CDR control")
    
    print("\n" + "=" * 70)


# ============================================================================
# Example 2: Selective CDR Control on Specific Channels
# ============================================================================

def example_selective_cdr_control():
    """
    Disable CDR only on specific channels of specific firefly devices.
    """
    print("=" * 70)
    print("Example 2: Selective CDR Control")
    print("=" * 70)
    
    reg = Registry(setup='tf', dev_path='/dev/ttyUSB2')
    
    # Disable CDR only on channels 1-4 of F1_5 (4-channel XCVR)
    f1_5 = _get_configured_firefly(reg, 'F1_5')
    if isinstance(f1_5, Firefly4):
        print(f"\nDisabling CDR on F1_5 channels 1-4...")
        try:
            f1_5.disable_cdr(channels=[1, 2, 3, 4])
            f1_5.print_status()
        except EXPECTED_HARDWARE_ERRORS as exc:
            _report_error("Could not update F1_5", exc)
    
    # Disable CDR only on Tx side of F1_1
    f1_1 = _get_configured_firefly(reg, 'F1_1')
    if isinstance(f1_1, (Firefly4, Firefly12)):
        print("\nDisabling CDR on F1_1 Tx only...")
        try:
            f1_1.disable_cdr(tx=True, rx=False)
            f1_1.print_status()
        except EXPECTED_HARDWARE_ERRORS as exc:
            _report_error("Could not update F1_1", exc)
    elif isinstance(f1_1, FireflyTx):
        print("\nDisabling CDR on Tx-only device F1_1...")
        try:
            f1_1.disable_cdr()
            f1_1.print_status()
        except EXPECTED_HARDWARE_ERRORS as exc:
            _report_error("Could not update F1_1", exc)
    elif f1_1 is not None:
        print(f"  ⚠ Skipping F1_1 Tx control: configured as {type(f1_1).__name__}")
    
    print("\n" + "=" * 70)


# ============================================================================
# Example 3: Check CDR Status Before and After
# ============================================================================

def example_cdr_status_check():
    """
    Read and display CDR enable status for all channels before and after
    disabling.
    """
    print("=" * 70)
    print("Example 3: CDR Status Check")
    print("=" * 70)
    
    reg = Registry(setup='tf', dev_path='/dev/ttyUSB2')
    
    # Check status for F2_5 (4-channel XCVR)
    f2_5 = _get_configured_firefly(reg, 'F2_5')
    if isinstance(f2_5, Firefly4):
        print(f"\nF2_5 CDR Status (BEFORE):")
        for ch in range(1, 5):
            status = f2_5.get_cdr_status(ch)
            print(f"  Ch {ch}: TX={status.get('tx')}, RX={status.get('rx')}")
        
        # Disable CDR
        f2_5.disable_cdr()
        
        print(f"\nF2_5 CDR Status (AFTER):")
        for ch in range(1, 5):
            status = f2_5.get_cdr_status(ch)
            print(f"  Ch {ch}: TX={status.get('tx')}, RX={status.get('rx')}")
    
    print("\n" + "=" * 70)


# ============================================================================
# Example 4: Clock Status Monitoring
# ============================================================================

def example_clock_monitoring():
    """
    Monitor Si5395 clock status using symbolic register names and
    convenience methods.
    """
    print("=" * 70)
    print("Example 4: Clock Status Monitoring")
    print("=" * 70)
    
    reg = Registry(setup='tf', dev_path='/dev/ttyUSB2')
    
    # Iterate over all clocks
    for name, clock in reg.clocks.items():
        print(f"\nClock {name}:")
        
        # Method 1: Using convenience properties
        print(f"  Device ID: 0x{clock.get_device_id():04X}")
        print(f"  Revision: {clock.get_device_rev()}")
        
        # Method 2: Check specific status flags
        if clock.is_locked():
            print(f"  ✓ PLL is locked")
        else:
            print(f"  ✗ PLL is NOT locked")
        
        if clock.is_in_holdover():
            print(f"  ⚠ In holdover mode")
        
        # Method 3: Check for loss of signal on inputs
        for i in range(4):
            if clock.has_loss_of_signal(i):
                print(f"  ⚠ LOS on Input {i}")
        
        # Method 4: Read raw status register using symbolic name
        status = clock.read_reg(Si5395Reg.LOL_HOLD_STATUS)
        print(f"  LOL Status Register: 0x{int.from_bytes(status, 'big'):04X}")
    
    print("\n" + "=" * 70)


# ============================================================================
# Example 5: LGA80D Voltage Monitoring
# ============================================================================

def example_lga80d_monitoring():
    """
    Read voltage and status from all LGA80D DC-DC converters.
    """
    print("=" * 70)
    print("Example 5: LGA80D Voltage Monitoring")
    print("=" * 70)
    
    reg = Registry(setup='tf', dev_path='/dev/ttyUSB2')
    
    # Iterate over all LGA80D devices
    for name, lga in reg.lga80d.items():
        print(f"\nLGA80D {name}:")
        
        # Read supply voltage
        try:
            voltage = lga.voltage
            print(f"  Output Voltage: {voltage:.2f} V")
        except EXPECTED_HARDWARE_ERRORS as exc:
            _report_error("Could not read voltage", exc)
        
        # Read status
        try:
            status = lga.read_status()
            state = "FAULT" if status.has_faults else "OK"
            print(f"  Status: {state}")
            print(f"    WORD: 0x{status.word:04X}")
            print(
                "    VOUT: 0x{:02X}, IOUT: 0x{:02X}, INPUT: 0x{:02X}, "
                "TEMP: 0x{:02X}, CML: 0x{:02X}, MFR: 0x{:02X}".format(
                    status.vout,
                    status.iout,
                    status.input,
                    status.temperature,
                    status.cml,
                    status.manufacturer,
                )
            )
        except EXPECTED_HARDWARE_ERRORS as exc:
            _report_error("Could not read status", exc)
    
    print("\n" + "=" * 70)


# ============================================================================
# Example 6: Complete System Status Report
# ============================================================================

def example_system_status_report():
    """
    Generate a complete status report for all devices in the system.
    """
    print("=" * 70)
    print("Example 6: Complete System Status Report")
    print("=" * 70)
    
    reg = Registry(setup='tf', dev_path='/dev/ttyUSB2')
    
    print(f"\nRegistry initialized with TF configuration")
    print(f"  Clocks: {len(reg.clocks)}")
    print(f"  Fireflies: {len(reg.fireflies)}")
    print(f"  LGA80D: {len(reg.lga80d)}")
    
    # Clock summary
    print("\n--- Clock Summary ---")
    for name, clock in reg.clocks.items():
        locked = "LOCKED" if clock.is_locked() else "UNLOCKED"
        print(f"  {name}: {locked}, ID=0x{clock.get_device_id():04X}")
    
    # Firefly summary
    print("\n--- Firefly Summary ---")
    for loc, device in reg.fireflies.items():
        dev_type = type(device).__name__
        part_id = device.part_id
        print(f"  {loc} ({dev_type}): part_id={part_id}")
    
    # LGA80D summary
    print("\n--- LGA80D Summary ---")
    for name, lga in reg.lga80d.items():
        print(f"  {name}")
    
    print("\n" + "=" * 70)


# ============================================================================
# Example 7: Firefly Device Identification
# ============================================================================

def example_firefly_identification():
    """
    Identify firefly device types and read their part-number fields.
    """
    print("=" * 70)
    print("Example 7: Firefly Device Identification")
    print("=" * 70)
    
    reg = Registry(setup='tf', dev_path='/dev/ttyUSB2')
    
    # Group devices by type
    xcvr_4ch = []
    tx_only = []
    rx_only = []
    xcvr_12ch = []
    
    for loc, device in reg.fireflies.items():
        part_id = device.part_id
        
        if isinstance(device, Firefly4):
            xcvr_4ch.append((loc, part_id))
        elif isinstance(device, Firefly12):
            xcvr_12ch.append((loc, part_id))
        elif isinstance(device, FireflyTx):
            tx_only.append((loc, part_id))
        elif isinstance(device, FireflyRx):
            rx_only.append((loc, part_id))
    
    print(f"\n4-Channel XCVRs: {len(xcvr_4ch)}")
    for loc, part_id in xcvr_4ch:
        print(f"  {loc}: {part_id}")
    
    print(f"\n12-Channel XCVRs: {len(xcvr_12ch)}")
    for loc, part_id in xcvr_12ch:
        print(f"  {loc}: {part_id}")
    
    print(f"\nTx-Only: {len(tx_only)}")
    for loc, part_id in tx_only:
        print(f"  {loc}: {part_id}")
    
    print(f"\nRx-Only: {len(rx_only) }")
    for loc, part_id in rx_only:
        print(f"  {loc}: {part_id}")
    
    print("\n" + "=" * 70)


# ============================================================================
# Example 8: Register Access Patterns Comparison
# ============================================================================

def example_register_access_patterns():
    """
    Demonstrate three ways to access device registers.
    """
    print("=" * 70)
    print("Example 8: Register Access Patterns")
    print("=" * 70)
    
    reg = Registry(setup='tf', dev_path='/dev/ttyUSB2')
    clock = reg.get_clock('R0A')
    
    print("\nThree ways to read the LOL status register:")
    
    # Method 1: Symbolic name (RECOMMENDED)
    print("\n1. Using symbolic register name:")
    status1 = clock.read_reg(Si5395Reg.LOL_HOLD_STATUS)
    print(f"   clock.read_reg(Si5395Reg.LOL_HOLD_STATUS) = {status1.hex()}")
    
    # Method 2: Convenience method
    print("\n2. Using convenience property:")
    is_locked = clock.is_locked()
    print(f"   clock.is_locked() = {is_locked}")
    
    # Method 3: Raw hex address (legacy, but still works)
    print("\n3. Using raw hex address:")
    status3 = clock.read_reg(0x000E)
    print(f"   clock.read_reg(0x000E) = {status3.hex()}")
    
    print("\n" + "=" * 70)


# ============================================================================
# Main - Run all examples
# ============================================================================

def main():
    """Run every example, continuing past expected hardware failures."""
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "CM Interface Worked Examples" + " " * 25 + "║")
    print("╚" + "=" * 68 + "╝")
    print("\n")

    examples = (
        ("Disable CDR on all Fireflies", example_disable_cdr_all_fireflies),
        ("Selective CDR control", example_selective_cdr_control),
        ("CDR status check", example_cdr_status_check),
        ("Clock monitoring", example_clock_monitoring),
        ("LGA80D monitoring", example_lga80d_monitoring),
        ("System status report", example_system_status_report),
        ("Firefly identification", example_firefly_identification),
        ("Register access patterns", example_register_access_patterns),
    )
    failed = []

    for name, example in examples:
        try:
            example()
        except EXPECTED_EXAMPLE_ERRORS as exc:
            print()
            _report_error(f"{name} stopped", exc)
            print("  Continuing with the next example.")
            failed.append(name)

    if failed:
        print(f"\n⚠ Completed with {len(failed)} example(s) stopped by hardware errors:")
        for name in failed:
            print(f"  - {name}")
        return 1

    print("\n✓ All examples completed!\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
