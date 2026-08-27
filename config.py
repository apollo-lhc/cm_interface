"""Board‑specific preset configurations.

Two typical board layouts are supported:

* **TF** – Track‑finder configuration
* **IT_DTC** – Inner‑tracker DTC configuration

Each preset provides the address map for clocks, firefly modules, and LGA80D devices.
If a board uses a custom wiring, callers can instantiate ``Registry`` without a preset and manually adjust the mappings.
"""

from enum import Enum
from typing import Dict, Any

class BoardSetup(Enum):
    TF = "tf"
    IT_DTC = "it_dtc"
    CUSTOM = "custom"

# Placeholder address maps – replace with the actual addresses for each layout.
# The structure mirrors the arguments used in Registry._populate.
PRESET_CONFIGS: Dict[BoardSetup, Dict[str, Any]] = {
    BoardSetup.TF: {
        "clocks": {
            "R0A": 0x10,
            "R0B": 0x11,
            "R1A": 0x12,
            "R1B": 0x13,
            "R1C": 0x14,
        },
        "firefly": {
            # Full‑duplex 4‑channel transceivers
            "F1_5": "4", "F1_6": "4", "F2_5": "4", "F2_6": "4",
            # Rx‑only ports (F1_1‑F1_4) – assumed standard variant unless overridden
            "F1_1": "Rx", "F1_2": "Rx", "F1_3": "Rx", "F1_4": "Rx",
            # Tx‑only port (F2_3) – assumed standard variant unless overridden
            "F2_3": "Tx",
            # Variant overrides (CERN‑B parts)
            "variant": {
                "F1_2": "cern",
                "F1_3": "cern",
                "F1_4": "cern",
                "F2_1_Rx": "cern", "F2_1_Tx": "cern",
                "F2_2_Rx": "cern", "F2_2_Tx": "cern"
            }
        },
        "lga80d": {
            "F1VCCINT1": 0x40,
            "F1VCCINT2": 0x41,
            "F2VCCINT1": 0x42,
            "F2VCCINT2": 0x43,
            "F1AVTT/CC": 0x44,
            "F2AVTT/CC": 0x45,
            "3V3/1V8": 0x46,
        },
    },
    BoardSetup.IT_DTC: {
        "clocks": {
            "R0A": 0x20,
            "R0B": 0x21,
            "R1A": 0x22,
            "R1B": 0x23,
            "R1C": 0x24,
        },
        "firefly": {
            "F1_5": "4", "F1_6": "4", "F2_5": "4", "F2_6": "4",
            # Tx‑only port (F1_1) – standard unless overridden
            "F1_1": "Tx",
            # Variant overrides (CERN‑B parts)
            "variant": {
                "F1_2_Tx": "cern", "F1_2_Rx": "cern",
                "F1_3_Tx": "cern", "F1_3_Rx": "cern",
                "F1_4_Tx": "cern", "F1_4_Rx": "cern",
                "F2_2_Tx": "cern", "F2_2_Rx": "cern",
                "F2_3_Tx": "cern", "F2_3_Rx": "cern",
                "F2_4_Tx": "cern", "F2_4_Rx": "cern"
            }
        },
        "lga80d": {
            "F1VCCINT1": 0x50,
            "F1VCCINT2": 0x51,
            "F2VCCINT1": 0x52,
            "F2VCCINT2": 0x53,
            "F1AVTT/CC": 0x54,
            "F2AVTT/CC": 0x55,
            "3V3/1V8": 0x56,
        },
    },
    BoardSetup.CUSTOM: {
        "clocks": {},
        "firefly": {},
        "lga80d": {},
    },
}
