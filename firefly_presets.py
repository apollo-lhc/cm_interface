"""Mutable firefly wiring presets.

Only the firefly layout varies between board types (TF, IT‑DTC) or
custom configurations.  The dictionaries are plain mutable ``dict`` objects
so they can be edited at runtime if needed.

Based on actual hardware configurations from ff_dump_names output.
"""

from enum import Enum
from typing import Mapping, Final

class BoardSetup(Enum):
    TF = "tf"
    IT_DTC = "it_dtc"
    CUSTOM = "custom"

# Each entry contains a ``firefly`` dict with location → type mapping.
# Types: 
#   "4" for full‑duplex 4‑channel transceiver
#   "Tx" for Tx‑only
#   "Rx" for Rx‑only
#   "both" for locations with separate Tx and Rx devices (used for CERN-B variants)
#
# TF Configuration (from ff_dump_names hardware output):
#   F1_1 to F1_4: Rx-only (standard ECUOR) - POPULATED
#   F2_3: Tx-only (standard ECUOT) - POPULATED
#   F1_5, F1_6, F2_5: 4-channel XCVR slots - EMPTY (------)
#   F2_6: 4-channel XCVR - POPULATED (OTP-237687-01)
#   F2_1, F2_2, F2_4: UNUSED (both Tx and Rx show ------)
#   NO CERN-B variants!
#
# IT-DTC Configuration (from ff_dump_names hardware", "both" with CERN-B variant (separate Tx and Rx)
#   F2_2, F2_3, F2_4: "both" with CERN-B variant (separate Tx and Rx)
#   F1_5, F1_6, F2_5, F2_6: 4-channel XCVR - all POPULATED
#   F2_1: UNUSED
#   12 CERN-B devices total

FIREfly_PRESETS: Final[Mapping[BoardSetup, dict]] = {
    BoardSetup.TF: {
        "firefly": {
            # 4-channel transceivers - ONLY F2_6 is populated
            "F2_6": "4",
            # F1_5, F1_6, F2_5 are EMPTY slots (not in config)
            
            # Rx-only (F1_1 to F1_4) - standard, NOT CERN-B
            "F1_1": "Rx", "F1_2": "Rx", "F1_3": "Rx", "F1_4": "Rx",
            
            # Tx-only (F2_3) - standard
            "F2_3": "Tx",
            
            # F2_1, F2_2, F2_4 are UNUSED - not in config
        },
        "variant": {},  # NO CERN-B variants in TF
    },
    BoardSetup.IT_DTC: {
        "firefly": {
            # 4-channel transceivers - all populated
            "F1_5": "4", "F1_6": "4", "F2_5": "4", "F2_6": "4",
            
            # F1_1: Tx-only (standard)
            "F1_1": "Tx",
            
            # F1_2, F1_3, F1_4: both Tx and Rx (CERN-B variants)
            "F1_2": "both", "F1_3": "both", "F1_4": "both",
            
            # F2_2, F2_3, F2_4: both Tx and Rx (CERN-B variants)
            "F2_2": "both", "F2_3": "both", "F2_4": "both",
            
            # F2_1 is UNUSED - not in config
        },
        "variant": {
            # CERN-B variants - both Tx and Rx at each location
            "F1_2_Tx": "cern", "F1_2_Rx": "cern",
            "F1_3_Tx": "cern", "F1_3_Rx": "cern",
            "F1_4_Tx": "cern", "F1_4_Rx": "cern",
            "F2_2_Tx": "cern", "F2_2_Rx": "cern",
            "F2_3_Tx": "cern", "F2_3_Rx": "cern",
            "F2_4_Tx": "cern", "F2_4_Rx": "cern",
        },
    },
    BoardSetup.CUSTOM: {"firefly": {}, "variant": {}},
}