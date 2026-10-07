# cm_interface TODO

Consolidated 2026-10-07 from a read-only audit of the plan docs in `../`, `FUTURE_IDEAS.md`
and the docs here. Only cm_interface-side work is tracked; firmware items appear as
dependencies. Statuses were checked against the code on that date, not against hardware,
except where marked HW-verified. Delete an item when it lands.

## Known hazards in shipped code

- **`read_persistent_log_entries()` issues no refresh** (UART7 plan BL-3). Harmless while
  firmware leaves capability bit 5 unset, because the client refuses the call. Fix before
  Phase 3 firmware lands.
- **`FPGA.write_reg` has no guard**, against the refuse-by-default rule in `CLAUDE.md`.
  Open decision 3 in `FPGA_GENERIC_INTERFACE_PLAN.md` (`unsafe=True` opt-in vs warning).

## LGA80D snapshot follow-ups

`LGA80D.read_snapshot`, `lga80d_dump.py --snapshot` and the firmware `SN` device are
hardware-verified (2026-10-07: `tests/hw/test_hw_lga80d_snapshot.py` passed, manual CLI
comparison, `stack_usage` and `log` checks OK). Not exercised: the short-block path (needs a
temporary DEBUG firmware build) and a capture on an absent or NACKing supply. Snapshot bytes 12-13 are a copy of the temperature word on this board and are deliberately not decoded.

## Actionable now, no firmware needed

- Clock programming Phases 1-2 (`clock_config.py` parser, `Clock.program`): pure Python,
  but blocked on P0-a (hardware dump settling paged vs flat EEPROM layout).
- Add an MCU hardware test under `tests/hw/` if a repeatable one is wanted
  (`exercise_mcu.py` is the only MCU hardware path today).

## Blocked on firmware or hardware

| Item | Source | Blocked by |
|---|---|---|
| FPGA profile layer (named registers, identity descriptor, GT-test adapter) | `FPGA_GENERIC_INTERFACE_PLAN.md` Phases 2-5 | a bitfile and a board with FPGAs; decisions 3-8 unsettled |
| TCA9555 layer A (3V8 select straps) | `MCU_IOEXPANDER_PLAN.md` §4.1 | firmware, map minor 4, a System-page offset (`0x78`+) |
| TCA9555 layers B, C (`IO` device; gated writes or named `0x7f` commands) | same, §4.2-4.3 | firmware; C needs maintainer sign-off |
| Clock `EE` device and load trigger (Phases 3-6) | `CLOCK_PROGRAMMING_PLAN.md` | firmware; `EE` unresolved against `MCU_CLI_GAP_PLAN.md` §3.4; page/code numbering stale |
| CERN-B CDR control | former `CDR_CONTROL_SUMMARY.md` (now in `../outdated/`) | CERN register documentation, then hardware |
| Staleness metadata (`updateTick`/`isFFStale`) as page `0x06` extension at `0x20`+ | UART7 plan §5 | unscheduled |

## Deferred

- **Phase 3, persistent error log (pages `0x30`/`0x31`),** with prerequisites B11
  (keep `errbuffer_get()` off the ProgCom path) and B13 (`field_span.size` is `uint8_t`).
  Deferred 2026-10-07: the error log needs substantial rework first. Log levels and the
  ring-buffer dump are the same project (`MCU_CLI_GAP_PLAN.md` §3.6); scope them
  together. Client work: `refresh_persistent_log()`, `McuStaleSnapshot`, control code 7.

## Decisions pending

- Clock plan: is the `EE` device dropped (as `MCU_CLI_GAP_PLAN.md` §3.4 says) or argued for?
- Clock plan: which control codes and status page to use. Page `0x06` is now Runtime;
  codes 5/6 are live; 7 is reserved for Phase 3; 8 is the next unreserved code.
- FPGA plan: raw-write guard (decision 3), and decisions 4-8.
- IO expander Phase C: may remote clients reset optics and clocks?
- Whether `design.md` keeps its historical Firefly memory-map section (issue #210).

## Done, for reference

- 2026-10-07: `Clock.write_reg` refuses `NVM_WRITE` (`0x00E3`) and `NVM_READ_BANK`
  (`0x00E4`), including multi-byte writes spanning them, unless `allow_nvm=True`
  (`ClockNvmWriteRefused`). `test_alarm_temp_device_enum_matches_firmware` added.
- UART7 Phase 1, 2a, 2b and prerequisites B1, B2, B6-B9. HW-verified 2026-10-05 and
  2026-10-07 (firmware `eb42c64`): clamps 50-100 °C and 1-10 %, persistence across reboot
  for temperature and voltage, mismatched-word repair. Not checked: a virgin board
  showing 5 %, and the queue-full reply. B10 is rated theoretical; B12 was withdrawn.
- FPGA generic interface Phase 1 (raw `FP` transport, `device/fpga.py`, `Registry.get_fpga`).
- Firefly4Cern removal; datasheet audit (~41 fixes, spot-checked 2026-10-07).
  Records: `../outdated/FIREFLY4CERN_REMOVAL.md`, `../cm-interface-datasheet-audit.md`.

## Plan documents

Live, in `../`: `MCU_UART7_IMPLEMENTATION_PLAN.md` (authority; Phase 3 pending),
`MCU_CLI_GAP_PLAN.md`, `MCU_IOEXPANDER_PLAN.md`, `CLOCK_PROGRAMMING_PLAN.md`,
`FPGA_GENERIC_INTERFACE_PLAN.md`. Wire contract: `MCU_REGISTER_MAP.md` here and
`MCU_Reg.h` in `cm_mcu`.
