# AGENTS.md

This repository does **not** define any custom Opencode agents. The framework is a pure Python library that can be leveraged by external agents or scripts.

## How agents can interact

- **General agents** (e.g., `explore` or `general`) can import `cm_interface` to query hardware state, generate register reports, or drive configuration workflows.
- **Specialized agents** (e.g., a build‑automation or CI agent) may use the `Registry` to verify that all expected devices are reachable before proceeding with a test run.
- Because the UART connection is opened lazily, an agent can safely instantiate the `Registry` on a development host where `/dev/ttyUL4` does not exist; the actual serial port will only be accessed when a read/write is performed on a device.
- **Debug logging** is available for troubleshooting: pass `debug=sys.stdout` or `debug=file_object` to `Registry()` to log all UART transactions.

## Architecture

The register configuration is split into:

- **Immutable core wiring** (`core_config.py`) — Fixed UART addresses for Si5395 clocks and LGA80D DC-DC converters
- **Mutable firefly presets** (`firefly_presets.py`) — Board-specific Firefly optical transceiver layouts (TF, IT-DTC, custom)
- **Register definitions** (`registers/*.json`) — Comprehensive JSON maps for all devices with addresses, sizes, and descriptions

## Known hardware telemetry behavior

- Target Zynq systems may run Python 3.6. Keep package source compatible with
  Python 3.6 syntax and APIs. Import `dataclass` and space-separated hexadecimal
  formatting through `compat.py`; do not require the external `dataclasses`
  backport, `typing.Final`, built-in generic annotations such as `tuple[int]`,
  or the Python 3.8 `bytes.hex(sep)` form.
- The TF Firefly preset contains only populated devices: standard Rx-only
  modules at `F1_1` through `F1_4`, a standard Tx-only module at `F2_3`, and a
  standard 4-channel transceiver at `F2_6`. `F1_5`, `F1_6`, and `F2_5` are
  empty and are intentionally absent. TF has no CERN-B variants.
- `Registry.get_firefly()` raises `KeyError` for a location absent from the
  selected preset. Inventory or optional-slot code should use
  `registry.fireflies.get(location)` and report a skip when it returns `None`.
- LGA80D `READ_FREQUENCY` values are reported in kHz (typical readings are
  457, 463, or 800 kHz).
- Unloaded LGA80D outputs have historically reported negative `READ_IOUT`
  values. Preserve the signed reading rather than clamping it to zero; use the
  PMBus status registers to determine whether the supply is reporting a fault.
- `LGA80D.read_status()` returns the structured `LGA80DStatus` dataclass, not
  a scalar. Use `status.has_faults` for the summary and format `status.word` as
  a 16-bit value; the category fields are 8-bit values.
- Worked hardware examples should catch expected `CMError`, OS/UART timeout,
  and setup lookup failures at an appropriate device/example boundary. Include
  chained exception text so an MCU reason such as `Firefly not enabled` is not
  hidden, continue independent operations, and do not broadly suppress
  unexpected programming errors such as `TypeError`.

## MCU and FPGA endpoint status

`Registry` currently creates `mcu`, `fpgas["F1"]`, and `fpgas["F2"]` for
every setup. They are also available through `get_mcu()` and `get_fpga()`.
Creating these objects does not prove that the deployed MCU firmware or FPGA
bitfile implements the corresponding endpoint; access remains lazy until the
first transaction.

### MCU endpoint

The Python `MCU` client for ProgCom device `MC 0` follows
`MCU_REGISTER_MAP.md` (map major version 1) for CM REV2 and REV3:

- `system_info` verifies magic `CMCU` and register-map major version 1, then
  returns map version, hardware revision, capability and health masks, board
  ID, uptime, reset cause, and firmware Git version. There is no watchdog bit
  or watchdog registers — the watchdog is unused firmware-wide;
- `read_adc()` / `adc_readings` returns a 21-channel snapshot of raw
  little-endian IEEE-754 binary16 values; an unpublished channel reads back
  as `NaN` rather than carrying a separate validity bitmap. A single atomic
  4-byte-aligned read per channel means nothing can tear, so there is no
  generation counter on this page;
- `read_power()` / `power` returns the Power page's FSM state, flags, PG
  masks, and per-supply state array. It uses an even/odd generation counter
  and retries if publication changes mid-read;
- `read_alarm()` / `alarm` returns the Alarm page's task states and
  temperature/voltage bitmaps. It has no generation counter — each field
  group (state bytes; temperature status/latch pair; the three voltage-alarm
  bytes) is updated atomically by firmware. Note the temperature
  status/latch pair is an 8-byte `read_block`, i.e. **two** 4-byte wire
  transactions, not one: the firmware-side group atomicity argument holds,
  but the pair is not read atomically over the wire;
- `read_alarm_config()` / `alarm_config` returns the Config page `0x05` alarm
  thresholds (read-only at map minor 2): four signed `int16` °C temperature
  thresholds and the voltage threshold in percent (`uint16` centi-percent on the
  wire). Five independent halfwords, one transaction each, no generation
  counter. Temperatures are not clamped on read;
- `read_runtime()` / `runtime` returns the Runtime page `0x06` (heap,
  system-stack headroom, ZynqMon transmit flag, FPGA DONE pins, RTC). The RTC
  is two independent words read time, date, time and retried on a rollover;
  `McuCoherencyError` if never stable. On map minor >= 1 firmware `system_info`
  also carries the Firefly masks, build type and build time, `None` on
  minor 0;
- `read_persistent_log_info()` and `read_persistent_log_entries()` read the
  frozen (offsets final, not yet firmware-served) persistent-log pages; and
- `send_control(McuControlCommand...)` writes the one-byte write-only
  Control-page command (`ASSERT`/`RELEASE_PROGCOM_POWER_INHIBIT`,
  `CLEAR_POWER_FAULT`, `CLEAR_ALARM_LATCHES`, `ZYNQMON_ENABLE_TRANSMIT`,
  `ZYNQMON_DISABLE_TRANSMIT`). `ZYNQMON_DISABLE_TRANSMIT` is sticky: nothing
  re-enables it but the CLI or a reboot.

There is no `AdcTarget`/ADC-target page — it does not exist in the register
map. Do not infer support from an enum member alone: the client now enforces
this, checking the firmware capability mask and raising
`McuCapabilityUnavailable` before issuing a read. `check_capability=False`
bypasses it; `capabilities(refresh=True)` re-reads the cached mask.

**Done:** the Python client and memory-backed unit tests, and the matching
firmware — `cm_mcu/projects/cm_mcu/MCU_Reg.c` and `MCU_Reg.h` — which serves
pages `0x00` System, `0x01` Power, `0x02` Alarm, `0x03` ADC, `0x05` Config
(read-only), `0x06` Runtime and `0x7f` Control at map minor 2. Nothing here establishes what is
installed on a given target; older firmware may still answer
`MCU device not implemented`.

**Outstanding** — all in `../MCU_UART7_IMPLEMENTATION_PLAN.md`:

- prerequisite fixes B1-B13 (B6, B8 and B9 are done);
- Phase 2b (Config page `0x05` write path; needs maintainer sign-off; the
  read-only half is done), Phase 3 (persistent log). Pages `0x30`/`0x31` are frozen but **not**
  served: reads return `e invalid MCU page`.

`MCU_REGISTER_MAP.md` (in this directory) plus `MCU_Reg.h` are the
authoritative wire contract. The superseded plans in `../outdated/` have
wrong offsets; do not use them.

The register map is hand-synced across `MCU_Reg.h`, `device/mcu.py` and
`MCU_REGISTER_MAP.md` with no generator. `tests/test_wire_contract.py` parses
the firmware header and asserts the Python enums match it; run it whenever you
touch any of the three.

### FPGA endpoints

The Python Phase-1 raw client is implemented for ProgCom `FP 0` (F1) and
`FP 1` (F2):

- page 0 byte addresses `0x00` through `0xff` are accepted;
- one transaction transfers one to four bytes, while `read_block()` splits a
  longer sequential read into bounded transactions;
- byte writes preserve caller-provided wire order; integer writes require an
  explicit size and are encoded little-endian;
- `probe(reg)` returns `FPGAProbeResult` without raising for `CMError`; its
  default register 0 is known safe only for the currently deployed
  frequency-test bitfile; and
- an explicit chained `ADDR_ACK_ERROR` is translated to
  `FPGAInterfaceUnavailable`. Data-phase, timeout, and ambiguous NACK errors
  remain `RegisterAccessError`.

Raw FPGA generic register reads and writes through the MCU ProgCom transport
have been tested successfully on hardware. This validates the Phase-1 generic
access path; it does not validate a universal register map, since register
semantics still belong to the loaded bitfile.

The client is intentionally unprofiled. It provides no bitfile identity,
register names, permissions, decoding, or protection against semantically
unsafe writes. The deployed MCU firmware must provide the `FP` transport and
the loaded bitfile must instantiate a compatible generic I2C endpoint. A
missing bitfile endpoint is expected and is not automatically a board fault.

**Done:** Phase 1 raw transport. **Outstanding:** profiles, identity, named
registers, permissions and the VU13P adapter — see
`../FPGA_GENERIC_INTERFACE_PLAN.md`.

### FPGA generic-port hardware facts

The CM REV3 schematic, sheet 38, and the current `clk_freq_fpga_cmd` establish:

- the ports share MCU I2C5 and TCA9548A U46 (`0x70`) with FPGA SYSMON;
- F2 generic is mux channel 0, F2 SYSMON channel 1, F1 generic channel 2,
  and F1 SYSMON channel 3;
- U46 is also the 3.3-V/1.8-V level boundary, with downstream pull-ups to
  1.8 V;
- generic modules are instantiated by the bitfile and are unavailable before
  FPGA configuration, while SYSMON remains available whenever FPGA power is
  present;
- the example frequency-counter endpoint uses slave `0x2b`, one-byte register
  addresses, and 32-bit words at four-byte strides, but `0x2b` is an FPGA
  design convention rather than a board-strapped address;
- FPGA pin B29 is strapped high on F1 and low on F2, so a common bitfile can
  expose or validate its physical position; and
- `/I2C_RESET_FPGAS` resets the shared mux, so generic-port error recovery
  must not toggle it without coordinating with SYSMON monitoring.

The implemented Python layer stops at the raw `FPGA` objects for F1/F2.
Validated JSON profiles, named-register decoding, access permissions, and
high-level diagnostics remain future work. Future bitfiles should expose a
small read-only descriptor containing magic, interface ABI, profile/design ID,
register-map hash, build ID, capabilities, and optionally the F1/F2 position.
Do not guess a bitfile by reading arbitrary registers.

The concrete project
`/nfs/cms/hw/wittich/25G_2/vu13p_ibert_25g` shows how complex instances fit
this design. Its `mcu/gt_test_mcu_protocol.h` is a bitfile-owned portable wire
protocol, and `mcu/cm_mcu/GTTestEngine.c/.h` are an optional high-level MCU
adapter for commit-last requests and coherent result collection. A matching
Python profile should reuse those semantics. The adapter should sit above the
common generic-FPGA routing helper rather than duplicate mux/semaphore code.
The design occupies `0xa0`-`0xff`, so that range cannot be reserved for a
universal identity descriptor; select a non-conflicting common-header or
banking convention only after comparing real designs.

Generic transactions must acquire the same I2C5 semaphore as SYSMON, protect
mux selection and cleanup, and release it on every path. Raw operations should
normally contain one short transfer. A bitfile-specific MCU service may group
a bounded set of transfers for commit/coherency, but must never hold I2C5
across sleeps, FPGA test execution, or frequency-counter accumulation. A
missing generic endpoint is an expected bitfile condition, not automatically
a board fault.

## Registry Usage

```python
from cm_interface.registry import Registry

# Choose the setup, UART, timeout, and debug stream on the first construction.
reg = Registry(setup='tf')

# Registry() with no setup instead creates an empty Firefly layout while still
# providing clocks, LGA80Ds, the MCU, and both FPGA objects.

# Access devices
clock = reg.get_clock('R0A')          # Si5395 clock generator
firefly = reg.get_firefly('F2_6')     # populated TF Firefly4 transceiver
lga80d = reg.get_lga80d('F1VCCINT1') # LGA80D DC-DC converter
mcu = reg.get_mcu()                   # ProgCom MC 0 client
f1 = reg.get_fpga('F1')               # ProgCom FP 0 raw client
f2 = reg.get_fpga('F2')               # ProgCom FP 1 raw client
```

## Register Access

The common register API represents a page and offset as one 16-bit integer.
The MCU client uses this as `page << 8 | offset`; clocks use it for their
native pages. The raw FPGA clients deliberately reject nonzero pages and
accept only `0x00` through `0xff`:

```python
# Read from page 0x00 (default page)
status = clock.read_reg(0x000E)  # LOL_HOLD_STATUS

# Read from page 0x02 (DESIGN_ID) - no manual page switch needed!
design_id = b''
for offset in range(8):
    design_id += clock.read_reg(0x026B + offset)

# Symbolic register names (recommended)
from cm_interface.device.si5395 import Si5395Reg
device_id = clock.read_reg(Si5395Reg.DEVICE_ID)
```

**Important:** Do not manually write to the PAGE register (0x0001). The UART bridge encodes the full 16-bit address in each command, automatically selecting the correct page.

## Register Documentation

Complete register maps are available in the `registers/` directory:

| File | Device | Registers |
|------|--------|-----------|
| `si5395.json` | Si5395 Clock Generator | 100+ (page-based) |
| `firefly12.json` | Firefly 12-channel | 24+ (lower + upper pages) |
| `firefly4.json` | Firefly 4-channel | 22+ |
| `firefly_cernb.json` | Firefly CERN-B variant | 25+ |
| `lga80d.json` | LGA80D DC-DC Converter | 63 PMBus commands |
