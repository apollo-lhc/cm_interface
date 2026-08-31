# Future ideas

## MCU and FPGA device interfaces

Two larger interface extensions have separate detailed plans in the parent
directory:

- `../MCU_DEVICE_PLAN.md` covers presenting the MCU as a device, including
  power-state diagnostics and command arbitration, alarms and ADC readings,
  reset/failure causes, and both volatile and persistent error histories.
  ADC telemetry reuses `ZynqMonTask`'s IEEE-754 binary16 representation in
  little-endian ProgCom byte order; raw ADC counts are intentionally omitted.
  Configured voltage-alarm targets are included with a target-valid bitmap.
  Clear commands retain CLI behavior: alarm-triggered shutdown normally
  removes the live cause before its retained latch is cleared, whereas power
  `clearfail` clears immediately and may permit the normal retry sequence when
  the Zynq request remains high. ProgCom reports successful queueing; clients
  observe completion through ordinary latch/FSM status. The persistent EEPROM
  log is only 64 words, so clients read the full logical ring and filter
  empty/erased sentinels. No maintained valid-entry count is needed; a
  mutation generation protects coherent multi-read snapshots.
  The MCU ProgCom device targets CM REV2 and REV3 only; REV1 support and an
  alternate REV1 transport are out of scope.
- `../FPGA_GENERIC_INTERFACE_PLAN.md` covers the generic I2C ports on F1 and
  F2.

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

## LGA80D snapshot support

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
lga.reset_snapshot(page=0)
```

The decoded fields are VIN, VOUT, IOUT, maximum IOUT, duty cycle, temperature,
switching frequency in kHz, and the VOUT, IOUT, input, temperature, CML,
manufacturer, and flash status bytes.

Snapshot decoding must preserve signed IOUT values. The current hardware has
historically reported negative current on unloaded outputs, so the interface
must not clamp those readings to zero.
