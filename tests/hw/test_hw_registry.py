"""Hardware-runnable check for R01: pinned wire indices.

This does not require register responses from real devices -- it only
checks the addresses Registry constructs from firefly_presets.py's
wire_index map -- but it lives under the same hardware opt-in gate as the
rest of this suite for consistency, since it still opens a real UART/
Registry pointed at a real device path.
"""


def test_registry_builds_without_error(hw_registry):
    assert hw_registry is not None


def test_every_configured_location_matches_pinned_wire_index(hw_registry):
    wire_index = hw_registry.firefly_layout.get("wire_index", {})
    assert wire_index, (
        "active preset has no wire_index map -- see firefly_presets.py "
        "(R01 fix)"
    )

    mismatches = []
    for loc, idx in wire_index.items():
        device = hw_registry.fireflies.get(loc)
        if device is None:
            mismatches.append(f"{loc}: expected a populated device, found none")
            continue
        expected_addr = 0x20 + idx
        if device.address != expected_addr:
            mismatches.append(
                f"{loc}: address 0x{device.address:02X} != "
                f"expected 0x{expected_addr:02X} (wire_index {idx})"
            )

    assert not mismatches, "wire-index mismatches:\n" + "\n".join(mismatches)
