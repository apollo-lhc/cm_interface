"""Hardware-runnable checks for the LGA80D SNAPSHOT read via the MCU ProgCom
``SN`` device (``LGA80D.read_snapshot``).

PMBus traffic is deliberately small. A capture makes the MCU write
SNAPSHOT_CONTROL (0xF3) = 0x01 (copy the stored snapshot into the readable
buffer; it is not an erase), so every test that captures is behind a second
gate and all captures share one budget (``CAPTURE_BUDGET``) and are paced at
``CAPTURE_SPACING_S``. There is no soak loop and nothing here erases a
snapshot. Run it on the known-good board, not on a board under failure
analysis.

Gates (see README.md):
  CM_INTERFACE_HW=1                   collect this suite at all
  CM_INTERFACE_HW_ALLOW_SNAPSHOT=1    allow the captures (writes F3h = 0x01)

Tests without the second gate only send malformed ``SN`` lines that the
firmware rejects before touching I2C. Run with -s to see the tables.

Optional: CM_INTERFACE_HW_SNAPSHOT_SUPPLY=<name> picks the supply used by the
repeat/compare tests (default: the first supply by index).
"""

import os
import time

import pytest

from cm_interface.core_config import CORE_CONFIG

ALLOW_SNAPSHOT = os.environ.get("CM_INTERFACE_HW_ALLOW_SNAPSHOT") == "1"
needs_snapshot_gate = pytest.mark.skipif(
    not ALLOW_SNAPSHOT,
    reason="set CM_INTERFACE_HW_ALLOW_SNAPSHOT=1 to allow SNAPSHOT captures "
           "(writes SNAPSHOT_CONTROL=0x01)",
)

# 14 (every supply and page once) + 3 (repeat) + 1 (direct-read compare)
# + 2 (cache semantics) = 20.
CAPTURE_BUDGET = 20
CAPTURE_SPACING_S = 1.0
# These only guard against a pathological stall (the UART timeout is 5 s), not
# typical speed. First hardware run: whole read_snapshot() 0.2-0.5 s typical,
# 1.1 s for the first capture; the capture/readout split was not measured.
MAX_CAPTURE_S = 2.0         # until the reply to the capture write
MAX_READ_SNAPSHOT_S = 3.0   # capture plus the eight cached reads

# Generous plausibility ranges, as in test_hw_lga80d.py.
INPUT_VOLTAGE_RANGE = (5.0, 16.0)
OUTPUT_VOLTAGE_RANGE = (0.0, 6.0)
TEMPERATURE_RANGE_C = (-40.0, 150.0)
# Allowed difference between a snapshot and direct reads taken right after it
# (the load and temperature move between the two).
TOLERANCE = {"vin": 0.2, "vout": 0.02, "iout": 0.5, "temp": 2.0, "freq": 5.0}

_state = {"count": 0, "last": 0.0}


def _supplies():
    """Supply names ordered by their MCU DC index."""
    return sorted(CORE_CONFIG.lga80d, key=lambda name: CORE_CONFIG.lga80d[name])


def _dev(name):
    return CORE_CONFIG.lga80d[name] - 0x40


def _chosen_supply():
    name = os.environ.get("CM_INTERFACE_HW_SNAPSHOT_SUPPLY") or _supplies()[0]
    assert name in CORE_CONFIG.lga80d, "unknown supply %r" % name
    return name


def _line(registry, text):
    """Send one raw ProgCom line and return the stripped reply."""
    registry.uart.write((text + "\n").encode("ascii"))
    return registry.uart.readline().decode("ascii").strip()


def _pace_and_count():
    if _state["count"] >= CAPTURE_BUDGET:
        pytest.fail("SNAPSHOT capture budget (%d) exhausted" % CAPTURE_BUDGET)
    wait = CAPTURE_SPACING_S - (time.monotonic() - _state["last"])
    if wait > 0:
        time.sleep(wait)
    _state["count"] += 1


def _read_snapshot(lga, page):
    """One paced, counted read_snapshot().

    Returns (snapshot, total seconds, seconds until the capture's reply). The
    first readline() of read_snapshot() is the reply to the capture write, so
    it is timed by wrapping the UART's readline for the duration of the call.
    """
    _pace_and_count()
    uart = lga.uart
    replies = []
    start = time.monotonic()

    def timed_readline():
        line = real_readline()
        replies.append(time.monotonic())
        return line

    real_readline = uart.readline
    uart.readline = timed_readline
    try:
        snap = lga.read_snapshot(page)
    finally:
        del uart.readline  # drop the instance override, restoring the method
    _state["last"] = time.monotonic()
    return snap, _state["last"] - start, replies[0] - start


def _raw_capture(registry, name, page):
    """One paced, counted raw capture write; returns the reply line."""
    _pace_and_count()
    reply = _line(registry, "w SN %X %X 0 01" % (_dev(name), page))
    _state["last"] = time.monotonic()
    return reply


def _print_snapshot(name, page, snap, seconds, capture_seconds=None):
    ext = snap.raw[12:14]
    temp = snap.raw[10:12]
    print(
        "%s page %d (%s): VIN %.4g V  VOUT %.4g V  IOUT %.4g A (max %.4g)  "
        "duty %.4g %%  T %.4g C  f %.4g kHz  status=%s flash=0x%02X  "
        "bytes12-13=%s (%s the temperature word)  raw=%s" % (
            name, page,
            "%.0f ms" % (seconds * 1000) if capture_seconds is None else
            "%.0f ms, capture reply after %.0f ms" % (seconds * 1000, capture_seconds * 1000),
            snap.input_voltage, snap.output_voltage,
            snap.output_current, snap.max_output_current, snap.duty_cycle,
            snap.temperature, snap.switching_frequency,
            "".join("%02X" % b for b in snap.raw[16:22]), snap.flash_status,
            ext.hex(), "equal to" if ext == temp else "different from",
            snap.raw.hex()))


@pytest.fixture(scope="module", autouse=True)
def sn_device_present(hw_registry):
    """Skip the module on firmware without the SN device. Sends an SN line the
    firmware rejects before any I2C (device number out of range)."""
    reply = _line(hw_registry, "w SN 10 0 00 01")
    if reply.startswith("e invalid device type"):
        pytest.skip("firmware has no SN device (answers: %r)" % reply)
    assert reply == "e invalid DCDC device number", reply


# --- no I2C: malformed lines the firmware rejects before touching the bus ----

def test_sn_rejects_invalid_device_and_page_without_i2c(hw_registry):
    cases = [
        ("w SN 10 0 00 01", "invalid DCDC device number"),
        ("w SN 0 2 00 01", "invalid DCDC page"),
        ("r SN 10 0 0 4", "invalid DCDC device number"),
        ("r SN 0 2 0 4", "invalid DCDC page"),
    ]
    for line, expected in cases:
        reply = _line(hw_registry, line)
        assert reply == "e " + expected, (line, reply)


def test_sn_rejects_malformed_capture_without_i2c(hw_registry):
    # a valid dev/page with a wrong address or payload must not start a capture
    for line in ("w SN 0 0 01 01",    # address must be 00
                 "w SN 0 0 00 02",    # data must be 01
                 "w SN 0 0 00 01 00"):  # exactly one data byte
        reply = _line(hw_registry, line)
        assert reply == "e invalid SN command", (line, reply)


# --- captures (budgeted, paced; second gate) ---------------------------------

@needs_snapshot_gate
def test_every_supply_and_page_captures_once(hw_registry):
    problems = []
    slowest = slowest_capture = 0.0
    for name in _supplies():
        lga = hw_registry.get_lga80d(name)
        for page in (0, 1):
            snap, seconds, capture_seconds = _read_snapshot(lga, page)
            slowest = max(slowest, seconds)
            slowest_capture = max(slowest_capture, capture_seconds)
            _print_snapshot(name, page, snap, seconds, capture_seconds)
            assert len(snap.raw) == 32
            if not INPUT_VOLTAGE_RANGE[0] <= snap.input_voltage <= INPUT_VOLTAGE_RANGE[1]:
                problems.append("%s p%d: VIN %s out of range" % (name, page, snap.input_voltage))
            if not OUTPUT_VOLTAGE_RANGE[0] <= snap.output_voltage <= OUTPUT_VOLTAGE_RANGE[1]:
                problems.append("%s p%d: VOUT %s out of range" % (name, page, snap.output_voltage))
            if not TEMPERATURE_RANGE_C[0] <= snap.temperature <= TEMPERATURE_RANGE_C[1]:
                problems.append("%s p%d: temperature %s out of range" % (name, page, snap.temperature))
    print("slowest capture reply: %.0f ms, slowest read_snapshot: %.0f ms"
          % (slowest_capture * 1000, slowest * 1000))
    assert not problems, "\n".join(problems)
    assert slowest_capture < MAX_CAPTURE_S, "capture took %.2f s" % slowest_capture
    assert slowest < MAX_READ_SNAPSHOT_S, "read_snapshot took %.2f s" % slowest


@needs_snapshot_gate
def test_repeated_capture_is_stable_and_live(hw_registry):
    """Three paced captures of one supply and page. Prints the differences so a
    human can see whether the data is live (small changes) or a stored record
    (identical). Only asserts that nothing jumps."""
    name = _chosen_supply()
    lga = hw_registry.get_lga80d(name)
    snaps = []
    for _ in range(3):
        snap, seconds, capture_seconds = _read_snapshot(lga, 0)
        _print_snapshot(name, 0, snap, seconds, capture_seconds)
        snaps.append(snap)
    print("identical raw bytes across all three: %s" % (len({s.raw for s in snaps}) == 1))
    for snap in snaps[1:]:
        assert abs(snap.input_voltage - snaps[0].input_voltage) < 1.0
        assert abs(snap.output_voltage - snaps[0].output_voltage) < 0.1
        assert abs(snap.temperature - snaps[0].temperature) < 5.0


@needs_snapshot_gate
def test_snapshot_agrees_with_direct_reads(hw_registry):
    name = _chosen_supply()
    lga = hw_registry.get_lga80d(name)
    for page in (0,):
        snap, seconds, capture_seconds = _read_snapshot(lga, page)
        direct = lga.read_telemetry(page)
        _print_snapshot(name, page, snap, seconds, capture_seconds)
        print("direct: %s" % (direct,))
        assert abs(snap.input_voltage - direct.input_voltage) <= TOLERANCE["vin"]
        assert abs(snap.output_voltage - direct.output_voltage) <= TOLERANCE["vout"]
        assert abs(snap.output_current - direct.output_current) <= TOLERANCE["iout"]
        assert abs(snap.temperature - direct.temperature) <= TOLERANCE["temp"]
        assert abs(snap.switching_frequency - direct.switching_frequency) <= TOLERANCE["freq"]


@needs_snapshot_gate
def test_cache_is_single_slot_and_span_checked(hw_registry):
    first, second = _supplies()[0], _supplies()[1]
    dev_a, dev_b = _dev(first), _dev(second)

    assert _raw_capture(hw_registry, first, 0) == "c"
    assert _raw_capture(hw_registry, second, 0) == "c"

    # the second capture replaced the first: reading the first supply fails
    reply = _line(hw_registry, "r SN %X 0 0 4" % dev_a)
    assert reply == "e no SN capture for dev/page", reply
    # same supply, other page, was never captured
    reply = _line(hw_registry, "r SN %X 1 0 4" % dev_b)
    assert reply == "e no SN capture for dev/page", reply

    # the last capture is readable; span checks use a 32-bit sum (no wraparound)
    reply = _line(hw_registry, "r SN %X 0 1C 4" % dev_b)
    assert reply.startswith("d ") and len(reply.split()) == 5, reply
    for off in ("1E", "1F", "20", "FD"):
        reply = _line(hw_registry, "r SN %X 0 %s 4" % (dev_b, off))
        assert reply == "e invalid SN span", (off, reply)
