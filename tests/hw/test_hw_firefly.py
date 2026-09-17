"""Hardware-runnable checks for FireFly optical transceivers.

Focused on the F03/F04/F05 CDR addressing/width fix. Identity fields are
read for every populated location as a basic wiring sanity check; the CDR
round trip under the write-gate exercises the real 2-byte read-modify-write
path fixed by F03/F04/F05 without disabling any channel.

Run with -s to see the printed identity/status output.

Set CM_INTERFACE_HW_NO_FIREFLIES=1 to skip this whole file -- a common unit-
qualification test condition is a board with no optical modules installed
at all, in which case the MCU refuses every FireFly ProgCom transaction with
an "e Firefly not enabled" error regardless of register addressing. That is
a board-population fact, not a regression in the fixes these tests check, so
this flag lets you skip cleanly instead of getting spurious failures.
"""

import os

import pytest

from cm_interface.device.firefly import FireflyRx, FireflyTx

NO_FIREFLIES = os.environ.get("CM_INTERFACE_HW_NO_FIREFLIES") == "1"

pytestmark = pytest.mark.skipif(
    NO_FIREFLIES,
    reason="CM_INTERFACE_HW_NO_FIREFLIES=1: no FireFly optics installed "
    "(unit-qualification test condition) -- skipping FireFly hardware checks",
)


def test_identity_fields_are_readable(hw_registry):
    problems = []
    for loc, device in hw_registry.fireflies.items():
        try:
            part_id = device.part_id
        except Exception as exc:  # noqa: BLE001 -- report, don't hide, any failure
            problems.append(f"{loc} ({type(device).__name__}): part_id raised {exc!r}")
            continue
        print(f"{loc} ({type(device).__name__}): part_id={part_id!r}")
        if not part_id or not part_id.strip():
            problems.append(f"{loc} ({type(device).__name__}): part_id is empty")

    assert not problems, "\n".join(problems)


def test_cdr_status_is_readable_for_tx_rx(hw_registry):
    """Read-only: exercises the corrected CDR_LOL_STATUS_BASE addressing."""
    for loc, device in hw_registry.fireflies.items():
        if not isinstance(device, (FireflyTx, FireflyRx)):
            continue
        status = device.get_cdr_status()
        print(f"{loc} ({type(device).__name__}): cdr_status=0x{status:03X}")
        assert 0 <= status <= 0xFFF, (
            f"{loc}: cdr_status 0x{status:X} is outside the 12-bit range"
        )


def test_cdr_enable_round_trip_is_a_true_noop(hw_registry, allow_writes):
    """Regression test for F03/F04/F05.

    disable_cdr(channels=[]) is a genuine no-op: with an empty channel
    list, the validate-then-clear loop in _disable_cdr_12ch never executes,
    so the method reads the current 2-byte CDR_ENABLE_BASE mask and writes
    back the exact same bytes it just read. This exercises the real,
    previously-broken 2-byte read-modify-write wire path on real hardware
    without ever disabling a channel -- the CDR status read back afterward
    must equal the status read before.
    """
    for loc, device in hw_registry.fireflies.items():
        if not isinstance(device, (FireflyTx, FireflyRx)):
            continue

        before = device.get_cdr_status()
        device.disable_cdr(channels=[])
        after = device.get_cdr_status()

        print(
            f"{loc} ({type(device).__name__}): CDR round-trip "
            f"before=0x{before:03X} after=0x{after:03X}"
        )
        assert after == before, (
            f"{loc}: CDR status changed after a no-op round trip "
            f"(before=0x{before:03X}, after=0x{after:03X})"
        )
