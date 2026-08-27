# Future ideas

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
