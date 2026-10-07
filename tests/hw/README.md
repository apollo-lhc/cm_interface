# Hardware test suite

These tests talk to **real hardware** over the real UART -- actual FireFly
optical transceivers, an actual Si5395 clock generator, and an actual
LGA80D DC-DC converter on a CMS tracker command-module board. They are a
manual, human-run companion to the software unit tests in `tests/` (which
use a `FakeUART` and never touch hardware), meant to let a person with a
board in front of them confirm that the datasheet-audit bugfixes
(see `/nfs/cms/hw/wittich/int_test/cm-interface-datasheet-audit.md`) actually
hold up against real modules.

There is no MCU test here; `exercise_mcu.py` is the MCU hardware path.

**This suite is never run in CI** and is invisible to a plain
`pytest cm_interface/tests/` invocation: `tests/hw/conftest.py` uses
pytest's `collect_ignore_glob` hook to skip collecting every file in this
directory unless the environment opts in (see below). With the gate off,
these files are not even imported.

## Safety model: independent opt-in gates

1. **`CM_INTERFACE_HW=1`** -- required just to *collect* any test in this
   directory. Without it, `pytest` does not know these files exist.
2. **`CM_INTERFACE_HW_ALLOW_WRITES=1`** -- required in addition, only for
   the one test that writes to a register at all (a safe, reversible CDR
   round trip -- see below). Every other test in this suite is read-only.

3. **`CM_INTERFACE_HW_ALLOW_SNAPSHOT=1`** -- required for the LGA80D
   SNAPSHOT captures in `test_hw_lga80d_snapshot.py`. A capture makes the MCU
   write `SNAPSHOT_CONTROL` (0xF3) = 0x01 (copy the stored snapshot into the
   readable buffer; not an erase), so those tests have their own gate and a
   fixed budget of 20 paced captures (1 s apart) in total. Without it only
   the two no-I2C rejection tests in that file run.

Two more environment variables configure *which* hardware to talk to:

- **`CM_INTERFACE_DEV_PATH`** (default `/dev/ttyUL4`) -- the UART device
  path, passed straight to `Registry(dev_path=...)`.
- **`CM_INTERFACE_BOARD_SETUP`** (default `tf`) -- the board preset from
  `firefly_presets.py`'s `BoardSetup` (`tf` or `it_dtc`).

And one more that describes board *population*, not connectivity:

- **`CM_INTERFACE_HW_NO_FIREFLIES=1`** -- skip `test_hw_firefly.py` entirely.
  A board with no optical modules installed at all is a normal unit-
  qualification test condition; the MCU refuses every FireFly ProgCom
  transaction with `e Firefly not enabled` regardless of register
  addressing, which would otherwise show up as spurious failures unrelated
  to the F03/F04/F05 fixes those tests check. `test_hw_registry.py`,
  `test_hw_si5395.py`, and `test_hw_lga80d.py` are unaffected by this flag
  since they don't depend on FireFly modules being present.

## Running it

From the `int_test` parent directory (so the `cm_interface` package
resolves, matching how the software suite is run):

```sh
# Read-only checks only (identity, telemetry, wire-index, CDR status reads):
CM_INTERFACE_HW=1 CM_INTERFACE_BOARD_SETUP=tf \
    python -m pytest cm_interface/tests/hw/ -v -s

# Include the safe CDR round-trip write test:
CM_INTERFACE_HW=1 CM_INTERFACE_HW_ALLOW_WRITES=1 CM_INTERFACE_BOARD_SETUP=tf \
    python -m pytest cm_interface/tests/hw/ -v -s

# Unit-qualification bench with no optical modules installed:
CM_INTERFACE_HW=1 CM_INTERFACE_HW_NO_FIREFLIES=1 CM_INTERFACE_BOARD_SETUP=tf \
    python -m pytest cm_interface/tests/hw/ -v -s

# Against a different board / device path:
CM_INTERFACE_HW=1 CM_INTERFACE_BOARD_SETUP=it_dtc CM_INTERFACE_DEV_PATH=/dev/ttyUSB2 \
    python -m pytest cm_interface/tests/hw/ -v -s
```

`-s` is recommended: several checks (clock health, LGA80D telemetry, CDR
status) are legitimately environment-dependent (is an input clock
connected? is a supply intentionally off?) and are printed for a human to
read rather than hard-asserted, alongside the hard assertions that must
always hold.

## What each file checks, and why

- **`test_hw_registry.py`** (R01) -- every populated FireFly location's
  UART address matches the pinned `wire_index` map in `firefly_presets.py`.
  Needs a `Registry` object but not real device responses, so it runs even
  if the serial port is unavailable.
- **`test_hw_si5395.py`** (S01) -- `Clock.get_device_id()` must read back
  `0x5395` for every configured clock, a strong self-verifying check that
  the register addressing/framing is right at all. `is_ready()` is
  asserted to return a `bool` (the strict `== 0x0F` fix from S01); actual
  readiness/lock state is printed, not hard-asserted, since it depends on
  board power-up and input-clock wiring this suite doesn't control.
- **`test_hw_lga80d.py`** (L09) -- `read_telemetry()` values must fall in
  datasheet-plausible ranges. `has_faults` must be `False` whenever
  `STATUS_WORD` has only the `OFF` (bit 6) and/or `POWER_GOOD#` (bit 11)
  bits set -- the actual regression the audit found, checked against
  whatever state the real unit happens to be in when the test runs.
- **`test_hw_lga80d_snapshot.py`** -- the MCU ProgCom `SN` device and
  `LGA80D.read_snapshot`. Without the snapshot gate: malformed `SN` lines
  (bad device/page, wrong capture address or payload) are rejected by the
  firmware before any I2C. With it: every supply and page captured once
  (values printed, plausibility ranges asserted), three paced captures of
  one supply to see whether the data is live, a comparison against direct
  reads right after a capture (tolerances for load and temperature drift),
  and the one-slot cache and span checks (including the 32-bit-sum wrap
  guard, `r SN d p FD 4`). The first test skips the whole module if the
  firmware has no `SN` device. `CM_INTERFACE_HW_SNAPSHOT_SUPPLY` picks the
  supply for the repeat and compare tests. There is deliberately no soak
  loop (PMBus traffic on a board with a failure history) and no erase. The
  tests passed on hardware on 2026-10-07 (the capture reply took 70-733 ms,
  typically ~230 ms; the whole `read_snapshot` up to 861 ms).
- **`test_hw_firefly.py`** (F03/F04/F05) -- `part_id` is readable and
  non-empty for every populated location (basic wiring sanity). Under the
  write gate, `disable_cdr(channels=[])` is called as a **true no-op**: an
  empty channel list means the validate/clear loop never executes, so the
  fixed 2-byte `CDR_ENABLE_BASE` read-modify-write path is exercised on
  real hardware while writing back the exact bytes just read -- no channel
  is ever disabled. `Firefly4` CDR is intentionally not exercised here.

## What this suite deliberately does not do

It does not send any destructive command: no NVM writes, no
`Clock.reset()`, no LGA80D `OPERATION`/margin/limit writes, no snapshot
erase (`SNAPSHOT_CONTROL` 0x03), and no real FireFly channel disable. The one
LGA80D write is the gated `SNAPSHOT_CONTROL` = 0x01 capture above. The datasheet audit found several JSON-documented
write recipes that are outright wrong (see the audit's LGA80D and Si5395
sections) -- this suite does not attempt to exercise or validate those,
consistent with the audit's own conclusion that it does not authorize a
hardware write gate for those operations.

## Manual steps for the SNAPSHOT work (need the MCU CLI, UART0)

The host has no UART0, so these are done by a person at the CLI console, on the
known-good board, after the new firmware is flashed:

1. Take an `SN` capture (`test_hw_lga80d_snapshot.py -s`, or
   `lga80d_dump.py <SUPPLY> --snapshot`) and immediately run `snapshot <10*dev+page> 0`
   on the same supply and page. Fields should agree to rounding, apart from
   drift (about 1 C, a few hundredths of an amp).
2. `stack_usage` and `taskinfo` before and after the captures: ProgCom (`PRGCM`)
   free stack must stay comfortably above zero (baseline 234 words free).
3. `log` and `errorlog` after the captures: no new I2C errors.
4. `sn_all` still resets all snapshots with the outputs off (this erases the
   stored records; skip it on a board whose snapshots you want to keep).
5. Optional, needs a temporary DEBUG firmware build: point the block read at a
   shorter block register to exercise the short-read path (`e SN capture
   failed`, CLI prints zeros).
