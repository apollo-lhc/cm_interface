"""Immutable wiring configuration for all boards.

Clocks (Si5395, plus an Si5341 at R0A on Rev2) and DC‑DC converters (LGA80D) have fixed UART addresses that are
soldered onto the PCB.  These mappings never change at runtime – they are stored
in a frozen dataclass so any accidental mutation raises an error.
"""

from typing import Mapping

from .compat import dataclass

@dataclass(frozen=True)
class CoreConfig:
    clocks: Mapping[str, int]
    lga80d: Mapping[str, int]

# Global constant – the single source of truth for wiring.
CORE_CONFIG: CoreConfig = CoreConfig(
    clocks={
        "R0A": 0x10,
        "R0B": 0x11,
        "R1A": 0x12,
        "R1B": 0x13,
        "R1C": 0x14,
    },
    lga80d={
        # Keep these synthetic addresses in the same order as the MCU's
        # pm_addrs_dcdc[] table.  The ASCII protocol transmits address - 0x40
        # as the zero-based device number.
        "3V3/1V8":    0x40,
        "F1VCCINT1": 0x41,
        "F1VCCINT2": 0x42,
        "F2VCCINT1": 0x43,
        "F2VCCINT2": 0x44,
        "F1AVTT/CC": 0x45,
        "F2AVTT/CC": 0x46,
    },
)


# Clock chip at each position, by board revision (MCU HARDWARE_REVISION).
# cm_mcu LocalTasks.c clk_moni2c_addrs[]: REV2 has an Si5341 (0x77) at R0A,
# REV3 has Si5395s (0x6b) everywhere. Rev1 firmware has no ProgCom.
CLOCK_CHIPS: Mapping[int, Mapping[str, str]] = {
    2: {"R0A": "si5341", "R0B": "si5395", "R1A": "si5395", "R1B": "si5395",
        "R1C": "si5395"},
    3: {"R0A": "si5395", "R0B": "si5395", "R1A": "si5395", "R1B": "si5395",
        "R1C": "si5395"},
}
