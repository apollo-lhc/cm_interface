# CLAUDE.md

Guidance for Claude Code sessions working in this repo. See `AGENTS.md` for
detailed hardware/MCU/FPGA notes, `README.md` for package layout and usage,
and `design.md` for the board-level hardware rationale — this file only
covers what isn't obvious from reading the code.

## Hard constraints

- **Python 3.6 compatibility.** Target Zynq systems may run 3.6. Use
  `compat.py` for `dataclass` and hex formatting; no `dataclasses` backport,
  `typing.Final`, builtin generics (`tuple[int]`), or `bytes.hex(sep)`.
- **`registers/*.json` are documentation only** — nothing in the package
  loads them at runtime. The real, authoritative behavior is always the
  `IntEnum`s and methods in `device/*.py`. If the two disagree, the JSON is
  wrong, not the code; fix the JSON to match, never the other way around.
- **Hardware-affecting operations default to refusing, not succeeding
  quietly.** e.g. `Registry.reset_all_lga80d_snapshots` requires
  `force=True` and independently checks the MCU power state machine every
  time. When adding a new method that writes to real hardware with a
  physical precondition, prefer that pattern over silently trusting the
  caller.

## Testing

- `tests/*.py` — pure software, `FakeUART`-based, no hardware. Run from the
  `int_test` parent directory: `pytest cm_interface/tests/`. Must stay
  hardware-free; never add a test here that opens a real serial port.
- `tests/hw/` — opt-in, hardware-runnable companion suite (env-var gated,
  see `tests/hw/README.md`). Keep new hardware checks here, not in the
  software suite.

## Provenance

A datasheet audit (`/nfs/cms/hw/wittich/int_test/cm-interface-datasheet-audit.md`)
found and fixed ~41 register-map bugs across the Firefly/Si5395/LGA80D
device modules and `registry.py`'s wire-index assignment. When touching
those files, check whether the audit or its fix plan already covered the
register/address in question before assuming a value is arbitrary.
