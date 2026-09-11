# MCU (`MC`) ProgCom register map

Wire contract for the `MC` ProgCom device. Hand-maintained (no generator) —
keep `projects/cm_mcu/MCU_Reg.h` (`cm_mcu`) and `device/mcu.py`
(`cm_interface`) in sync with this file by hand.

Device number: only `0` is valid. Multi-byte integers are little-endian. A
ProgCom transaction transfers 1-4 bytes. Any address/span not listed below
is a hole: it returns an explicit error, never a readable zero. Reads must
stay within a single declared field (no crossing into a neighbor or a hole).

Status column: **implemented** = firmware serves real data today. **frozen**
= offsets are final and safe to code against, but the firmware page doesn't
exist yet (reads currently return `invalid MCU page`). **deferred** = same
as frozen, but explicitly not next in line — no active plan to scope or
implement it soon.

## Page `0x00` — System — **implemented**

Read-only.

| Offset | Size | Field |
| --- | ---: | --- |
| `0x00` | 4 | ASCII magic `CMCU` |
| `0x04` | 1 | map major version (`1`) |
| `0x05` | 1 | map minor version (`0`) |
| `0x06` | 1 | hardware revision |
| `0x07` | 1 | ADC channel count (`21`) |
| `0x08` | 4 | capability bitmap |
| `0x0c` | 4 | health-summary bitmap |
| `0x10` | 4 | board ID |
| `0x14` | 4 | uptime, seconds |
| `0x18` | 4 | reset cause (raw, uncleared 32-bit `SysCtl` value) |
| `0x1c` | 16 | reserved |
| `0x40` | 20 | version string, NUL-padded (`git describe --dirty --always --tags`) |

Capability bits (bitmap at `0x08`):

| Bit | Name | Set? |
| ---: | --- | --- |
| 0 | `SYSTEM` | yes |
| 1 | `POWER` | yes |
| 2 | `ALARMS` | yes |
| 3 | `ADC` | yes |
| 4 | reserved | — |
| 5 | `PERSISTENT_LOG` | no |
| 6 | `CONTROLS` | yes |

A capability bit is set only once its page is fully implemented; a client
must not assume a page works just because its offsets are frozen here.

Health-summary bits (bitmap at `0x0c`):

| Bit | Name | Meaning |
| ---: | --- | --- |
| 0 | `POWER_FAULT` | power FSM is in `POWER_FAILURE` |
| 1 | `TEMPERATURE_ALARM` | any temperature-alarm status bit set |
| 2 | `VOLTAGE_ALARM` | any voltage-alarm status bit set |
| 3 | `ADC_ERROR` | always `0` — no source published yet |

There is no `WATCHDOG` bit and no watchdog registers on this page — the
watchdog is unused firmware-wide and intentionally excluded.

## Page `0x01` — Power — **implemented**

Read-only. Keeps a generation counter (bytes `0x00`-`0x03`), unlike pages
`0x02`/`0x03`: these fields are updated by one task across several points
within one ~25ms control-loop pass and only make sense read together (e.g.
`failed_mask` and the FSM-state transition it caused) — a genuinely
different situation from Alarm/ADC, where dropping the counter was safe.

| Offset | Size | Field |
| --- | ---: | --- |
| `0x00` | 4 | generation counter |
| `0x04` | 1 | power FSM state (0-10, see state list below) |
| `0x05` | 1 | flags bitmap (see below) |
| `0x06` | 2 | reserved |
| `0x08` | 4 | live power-good mask (freshest reading each cycle) |
| `0x0c` | 4 | expected power-good mask (full/final target; not a per-sequencing-level submask) |
| `0x10` | 4 | software-ignore mask |
| `0x14` | 4 | failed-supply mask (latched) |
| `0x18` | 1 | per-supply count (`12` on REV2/REV3) |
| `0x19` | 3 | reserved |
| `0x1c` | 12 | per-supply state, one byte each, index = PG-pin index (state values below) |

Power FSM states (offset `0x04`), in order 0-10:

```text
0 POWER_FAILURE   4 POWER_L1ON   8  POWER_L5ON
1 POWER_INIT      5 POWER_L2ON   9  POWER_L6ON
2 POWER_DOWN      6 POWER_L3ON   10 POWER_ON
3 POWER_OFF       7 POWER_L4ON
```

Flags bitmap (offset `0x05`):

| Bit | Name |
| ---: | --- |
| 0 | `BLADE_POWER_EN` |
| 1 | `CLI_INHIBIT` |
| 2 | `PROGCOM_INHIBIT` (set/cleared via page `0x7f`'s `ASSERT`/`RELEASE_PROGCOM_POWER_INHIBIT`) |
| 3 | `POWER_FAULT_LATCH` |
| 4 | `ALARM_SHUTDOWN_LATCH` |
| 5 | `F1_ENABLE` |
| 6 | `F2_ENABLE` |

Per-supply state values (offset `0x1c` array, one byte per supply):

```text
0 PWR_UNKNOWN   2 PWR_OFF        4 PWR_FAILED
1 PWR_ON        3 PWR_DISABLED
```

## Page `0x02` — Alarm — **implemented**

Read-only. No generation counter: each FSM-state byte is a single atomic
write from its own task; `status_T`/`warnLatch` are always updated together
by one task in one function call, and likewise for the three per-group
voltage bytes — each group is self-consistent by construction, and any
staleness between the temperature and voltage groups is ordinary,
expected staleness between two independently-scheduled 50ms tasks, not
tearing.

| Offset | Size | Field |
| --- | ---: | --- |
| `0x00` | 1 | temperature-alarm task FSM state (0-4, see below) |
| `0x01` | 1 | voltage-alarm task FSM state (same scale) |
| `0x02` | 2 | reserved |
| `0x04` | 4 | temperature status bitmap (bit0 TM4C, bit1 Firefly, bit2 FPGA, bit3 DCDC) |
| `0x08` | 4 | temperature warning-latch bitmap (same bit positions) |
| `0x0c` | 1 | voltage-alarm bitmask, general/common rails |
| `0x0d` | 1 | voltage-alarm bitmask, F1-specific rails |
| `0x0e` | 1 | voltage-alarm bitmask, F2-specific rails |
| `0x0f` | 1 | reserved |

Alarm task FSM states (offsets `0x00`/`0x01`):

```text
0 ALM_INIT
1 ALM_NORMAL
2 ALM_WARN
3 ALM_FAULT_ERRORING
4 ALM_FAULT_ERROR_CLEARED
```

## Page `0x03` — ADC sample — **implemented**

Read-only. No generation counter, no valid/error bitmap: ADC channels are
independent, and a single 2-byte value is read in one ProgCom transaction
from one atomic 4-byte-aligned `float` read, so nothing can tear. A channel
with no current reading is `NaN`, not a separate flag.

| Offset | Size | Field |
| --- | ---: | --- |
| `0x00`-`0x29` | 42 (21×2) | `binary16[21]` ADC values, channel order below |

ADC channel order (page `0x03`):

```text
0  VCC_12V          7  F1_AVTT          14 CUR_V_M3V3
1  VCC_M3V3         8  F1_VCCAUX        15 CUR_V_4V0
2  VCC_3V3          9  F2_VCCINT        16 CUR_V_F1VCCAUX
3  VCC_4V0         10  F2_AVCC          17 CUR_V_F2VCCAUX
4  VCC_1V8         11  F2_AVTT          18 F1_TEMP
5  F1_VCCINT       12  F2_VCCAUX        19 F2_TEMP
6  F1_AVCC         13  CUR_V_12V        20 TM4C_TEMP
```

## Page `0x30` — Persistent-log metadata — **deferred**

Read-only.

| Offset | Size | Field |
| --- | ---: | --- |
| `0x00` | 4 | format version (`1`) |
| `0x04` | 4 | capacity, 32-bit words (`64`) |
| `0x08` | 4 | mutation generation |
| `0x0c` | 4 | latest error code |
| `0x10` | 4 | continuation count for the latest event |

## Page `0x31` — Persistent-log entries — **deferred**

Read-only.

| Offset | Size | Field |
| --- | ---: | --- |
| `0x00`-`0xff` | 256 (64×4) | raw 32-bit entries, logical index 0 = newest |

Undecoded raw EEPROM words, in logical (not physical) order. No valid-entry
count is published — scan all 64 and filter erased/empty sentinels.

## Page `0x7f` — Control — **implemented**

Write-only. One command byte at offset `0x00`.

| Code | Command |
| ---: | --- |
| 1 | `ASSERT_PROGCOM_POWER_INHIBIT` |
| 2 | `RELEASE_PROGCOM_POWER_INHIBIT` |
| 3 | `CLEAR_POWER_FAULT` |
| 4 | `CLEAR_ALARM_LATCHES` |

`CLEAR_ALARM_LATCHES` matches the existing CLI's `alarm_ctl clear` exactly:
it sends the same `ALM_CLEAR_ALL` message to both `tempAlarmTask.xAlmQueue`
and `voltAlarmTask.xAlmQueue`, accepting the same partial-failure risk the
CLI already accepts today (two independent sends, no rollback if one
fails). There is no independent per-alarm-type clear — that would be more
granularity than any existing interface offers.

Queue sends for every command on this page are non-blocking (`0`-tick
timeout): a full queue is real backpressure and should surface to the
remote client immediately as `MCU_REG_QUEUE_FULL`, not stall the ProgCom
UART link waiting for space.

Each alarm-clear command targets exactly one alarm task's queue — there is
no combined "clear all alarms" command, so no command ever needs atomic
delivery across more than one queue.

## Error responses

| Condition | Response |
| --- | --- |
| device number != 0 | `invalid MCU device number` |
| unsupported/unimplemented page | `invalid MCU page` |
| address not inside a declared field | `invalid MCU address` |
| read/write crosses the end of a field, or length outside 1-4 | `invalid MCU read span` |
| write to a read-only page | `MCU register is read only` |
| read from a write-only page | `MCU register is write only` |
| valid request, queue-owned op not yet accepted | `MCU queue full` |
| transient unavailability | `MCU data busy` |
| anything else | `MCU internal error` |
