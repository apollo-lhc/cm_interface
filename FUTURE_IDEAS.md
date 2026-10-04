# Future ideas

## MCU device interface

**Done:** `MC 0` pages `0x00` System, `0x01` Power, `0x02` Alarm, `0x03` ADC
and `0x7f` Control, in firmware and in `device/mcu.py`. `MCU_REGISTER_MAP.md`
is the wire contract.

**Outstanding:** `../MCU_UART7_IMPLEMENTATION_PLAN.md` — prerequisite fixes
B1-B13, then Phase 1 (System additions, Runtime page `0x06`, Control
additions), Phase 2 (Config page `0x05`) and Phase 3 (persistent log
`0x30`/`0x31`, offsets frozen but not served). The analysis behind it is
`../MCU_CLI_GAP_PLAN.md`. The original MCU plans are in `../outdated/` and
their offsets are wrong.

## FPGA generic interface

**Done:** Phase 1, the raw `FP 0`/`FP 1` transport, tested on hardware.
**Outstanding:** everything above it, per `../FPGA_GENERIC_INTERFACE_PLAN.md`.

The FPGA interface should be implemented as a stable raw MCU transport and a
data-driven Python profile layer. The physical routing is fixed, but the
register map may change or disappear with the loaded bitfile. The MCU should
therefore know only how to reach F1/F2; named registers, types, permissions,
units, and diagnostic behavior belong to independently selected profiles.

Start with the register map used by `clk_freq_fpga_cmd`: slave `0x2b`, a
one-byte address, two read/write scratch words, and 37 frequency counters.
Treat `0x2b` as a bitfile-interface convention, not a hardware-strapped
address. Legacy bitfiles use explicit profiles; future designs should include
a reserved, side-effect-free identity descriptor for safe automatic profile
selection.

The generic ports share I2C5 and mux `0x70` with SYSMON. Each access must keep
the semaphore only for mux selection, one short transaction, and mux clear.
The existing frequency test should eventually be refactored so it does not
hold I2C5 during its roughly 1.2-second counter accumulation wait.

The VU13P GT-test project at
`/nfs/cms/hw/wittich/25G_2/vu13p_ibert_25g` is a concrete complex profile. It
keeps the clock-monitor ABI at `0x00`-`0x9f` and uses `0xa0`-`0xff` as a
commit/result mailbox. Its portable protocol header should be the reference
for typed Python request/result objects. Its optional `cm_mcu` adapter should
reuse the common FPGA bus helper and is appropriate for bounded compound
transactions; FPGA execution and completion waits must occur after releasing
I2C5. Since this mailbox occupies the previously suggested descriptor range,
a universal identity mechanism needs either a small agreed common header,
banked metadata, or profile-specific safe probes until a common ABI exists.

## TCA9555 I/O expanders

Full plan: `../MCU_IOEXPANDER_PLAN.md`. **Not started.** Layer A depends on
Phase 1 of the UART7 plan; B and C are independent of it.

CM REV3 carries six TCA9555 I/O expanders — two for the clock synthesizers
(schematic sheet 4.03) and two per FPGA for the optics (sheets 4.05/4.06).
They are reachable **only** from the interactive CLI's generic I2C commands;
ProgCom has no path to them, so `cm_interface` cannot see them at all. Bus,
mux, channel and address for all six are tabulated in the plan and were
cross-checked against firmware that already drives these parts. Note F1 sits
on I2C bus 4 and F2 on bus 3 — the inversion is verified in three places and
is the easiest detail here to get backwards.

The expanders carry Firefly presence and interrupt lines, the 3V8 rail enables
and their read-only select straps, the ganged per-FPGA Firefly reset, and the
clock-synth resets and input selects.

Proposed in three layers, to be shipped in order:

- **A — derived state, no new I2C.** Most of the practical value is already
  cached in MCU RAM at boot. The Firefly presence masks are already covered by
  Phase 1 of `../MCU_UART7_IMPLEMENTATION_PLAN.md`; this adds
  `f1_ff12xmit_4v0_sel` / `f2_ff12xmit_4v0_sel`, one `uint32_t` each. No mux,
  no bus access, nothing that can change board state. Should ride along with
  that Phase 1 rather than waiting for the rest.
- **B — a constrained `IO` device type, read-only.** Not the generic I2C verb
  rejected in `../MCU_CLI_GAP_PLAN.md` §3.4: a firmware-owned table that the
  device number indexes into, exactly as `DC`/`FF`/`CL`/`FP` already work, so
  the client never names a bus, mux or I2C address. Wire syntax
  `r IO <devnum> 0 <reg>`. Plus a `device/tca9555.py` class with named signals
  (active-low correction in exactly one place), six named registry instances,
  a documentation-only `registers/tca9555.json`, and a contract-test entry
  covering the six-row routing table.
- **C — writes. Needs sign-off; do not start without it.** The configuration
  registers `0x06`/`0x07` are a larger hazard than the resets: writing the
  wrong direction bit either floats a control line or drives against an
  external driver, and neither shows up as an I2C error. The plan recommends
  refusing `0x04`-`0x07` over ProgCom unconditionally, and exposing
  `ff_reset` / `clkreset` as named page-`0x7f` control commands rather than raw
  writes, because the firmware already owns the correct pulse sequence and the
  REV2/REV3 bit difference.

REV2 and REV3 differ in five ways that a naive implementation gets silently
wrong — reset pin, 3V8 mask, presence field width, 4-channel presence bit
positions, and strap width. They are tabulated in the plan. REV1 has no
ProgCom and is out of scope.

Open question worth settling before any of layer C: if the only operations
ever needed remotely are `ff_reset` and `clkreset`, skip raw writes entirely
and add two control commands instead.

## LGA80D snapshot support

**Done:** resetting the snapshot needs only ordinary register writes, so it is
implemented in Python — `LGA80D.reset_snapshot(page)`, `reset_all_snapshots()`
and `Registry.reset_all_lga80d_snapshots(force=True)`.

**Outstanding:** reading the snapshot. It needs the new firmware verb below
and is not covered by any current plan (`../MCU_CLI_GAP_PLAN.md` §3.5 lists it
as unreachable over ProgCom).

Expose the LGA80D's atomic 32-byte monitoring snapshot through the
programmatic UART interface. This must be a dedicated operation rather than a
large ordinary register read: PMBus command `0xEA` is an SMBus block read and
cannot be split into multiple four-byte transactions.

Proposed request:

```text
s DC <device> <page> <reset-after>
```

For example, capture device 1, page 0, without resetting the accumulated
snapshot afterward:

```text
s DC 1 0 0\n
```

The successful response uses the existing data marker followed by exactly 32
bytes:

```text
d XX XX XX ... XX\n
```

The MCU operation should hold the DCDC I2C semaphore for the complete
transaction, select the requested page, write `0x01` to `SNAPSHOT_CONTROL`
(`0xF3`), wait 20 ms, and issue an SMBus block read of `SNAPSHOT` (`0xEA`). If
`reset-after` is 1, it should then write `0x03` to `SNAPSHOT_CONTROL`. Resetting
must default to false because the device may reject it while the output is on.

The implementation will need a 32-byte data buffer and a UART response buffer
of at least 128 bytes. Ordinary reads and writes should retain their four-byte
limit. The snapshot helper must return transaction errors and verify that the
block contains exactly 32 bytes before a data response is sent.

The Python interface should decode the block into an immutable
`LGA80DSnapshot` value and expose:

```python
lga.read_snapshot(page=0, reset_after=False)
```

The decoded fields are VIN, VOUT, IOUT, maximum IOUT, duty cycle, temperature,
switching frequency in kHz, and the VOUT, IOUT, input, temperature, CML,
manufacturer, and flash status bytes.

Snapshot decoding must preserve signed IOUT values. The current hardware has
historically reported negative current on unloaded outputs, so the interface
must not clamp those readings to zero.
