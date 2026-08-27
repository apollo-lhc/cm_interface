"""Immutable wiring configuration for all boards.

Clocks (Si5395) and DC‑DC converters (LGA80D) have fixed UART addresses that are
soldered onto the PCB.  These mappings never change at runtime – they are stored
in a frozen dataclass so any accidental mutation raises an error.
"""

from dataclasses import dataclass
from typing import Mapping, Final

@dataclass(frozen=True)
class CoreConfig:
    clocks: Mapping[str, int]
    lga80d: Mapping[str, int]

# Global constant – the single source of truth for wiring.
CORE_CONFIG: Final[CoreConfig] = CoreConfig(
    clocks={
        "R0A": 0x10,
        "R0B": 0x11,
        "R1A": 0x12,
        "R1B": 0x13,
        "R1C": 0x14,
    },
    lga80d={
        "F1VCCINT1": 0x40,
        "F1VCCINT2": 0x41,
        "F2VCCINT1": 0x42,
        "F2VCCINT2": 0x43,
        "F1AVTT/CC": 0x44,
        "F2AVTT/CC": 0x45,
        "3V3/1V8":    0x46,
    },
)