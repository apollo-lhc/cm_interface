"""Hardware-runnable checks for the clock chips (Si5395; Si5341 at R0A on Rev2).

Focused on what this session's audit fix touched (S01: DEVICE_READY must
read exactly 0x0F, not merely nonzero) plus a strong, self-verifying wiring
sanity check (DEVICE_ID must read back the part the board revision implies,
0x5395 or 0x5341 -- if the register addresses, framing or the board revision
were wrong, this would almost certainly not match by accident).

Run with -s to see the printed telemetry.
"""

from cm_interface.core_config import CORE_CONFIG


def test_device_id_matches_board_revision(hw_registry):
    """Read-only wiring sanity check: DEVICE_ID must be the expected chip."""
    print(f"hw rev: {hw_registry.hw_rev}")
    mismatches = []
    for name in CORE_CONFIG.clocks:
        clock = hw_registry.get_clock(name)
        device_id = clock.get_device_id()
        print(f"{name}: {clock.CHIP} device_id=0x{device_id:04X}")
        if device_id != clock.EXPECTED_PN:
            mismatches.append(f"{name}: device_id=0x{device_id:04X}, "
                              f"expected 0x{clock.EXPECTED_PN:04X} ({clock.CHIP})")
    assert not mismatches, "\n".join(mismatches)


def test_is_ready_returns_a_bool(hw_registry):
    """Regression test for S01: is_ready() must be a strict ==0x0F check.

    We only hard-assert that the call succeeds and returns a bool -- whether
    a given clock is actually ready depends on board power-up state, which
    this suite doesn't control. The value is printed for a human to check.
    """
    for name in CORE_CONFIG.clocks:
        clock = hw_registry.get_clock(name)
        ready = clock.is_ready()
        assert isinstance(ready, bool), f"{name}: is_ready() returned {ready!r}, not a bool"
        print(f"{name}: is_ready()={ready}")
        if not ready:
            print(f"{name}: WARNING -- DEVICE_READY != 0x0F (device not ready)")


def test_health_snapshot(hw_registry):
    """Read and print the full health snapshot for a human to eyeball.

    Not hard-asserted beyond 'does not raise' -- lock state, holdover, and
    calibration flags are legitimately environment-dependent (e.g. no input
    clock connected in some bench setups).
    """
    for name in CORE_CONFIG.clocks:
        clock = hw_registry.get_clock(name)
        if not clock.is_ready():
            print(f"{name}: skipping .health -- device not ready")
            continue
        health = clock.health
        print(f"{name}: health={health}")
        print(f"{name}: is_locked()={clock.is_locked()}")
