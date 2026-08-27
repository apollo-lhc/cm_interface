from .uart import UART
from .device.si5395 import Clock
from .device.firefly import Firefly12, Firefly4, FireflyTx, FireflyRx, FireflyTxCern, FireflyRxCern, Firefly
from .device.lga80d import LGA80D
from .core_config import CORE_CONFIG
from .firefly_presets import FIREfly_PRESETS, BoardSetup
from typing import Optional, TextIO

class Registry:
    """Singleton registry that creates a single UART instance and populates
    concrete device objects.

    The registry can be instantiated with an optional ``setup`` argument to
    load a predefined board configuration (``'tf'`` or ``'it_dtc'``).  When
    ``setup`` is omitted or ``None``, the original generic mapping is used,
    allowing full manual control of the address maps.
    """
    _instance = None

    def __new__(cls, setup: str = None, debug: Optional[TextIO] = None, dev_path: str = "/dev/ttySL4"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init(setup, debug, dev_path)
        return cls._instance

    def _init(self, setup: str = None, debug: Optional[TextIO] = None, dev_path: str = "/dev/ttySL4"):
        self.uart = UART(debug=debug, dev_path=dev_path)
        # Instantiate device objects from CORE_CONFIG addresses
        self.clocks = {}
        self.lga80d = {}
        for name, addr in CORE_CONFIG.clocks.items():
            self.clocks[name] = Clock(self.uart, addr, name)
        for name, addr in CORE_CONFIG.lga80d.items():
            self.lga80d[name] = LGA80D(self.uart, addr, name)
        # Firefly wiring – mutable layout dict
        self.fireflies = {}
        self.firefly_layout = {}
        if setup:
            self.apply_preset(setup)
        else:
            # start with empty layout (user can modify later)
            self.firefly_layout = {}
        # Populate firefly devices based on current layout
        self._populate_fireflies_from_layout()

    # ---------------------------------------------------------------------
    # Preset handling
    # ---------------------------------------------------------------------
    def apply_preset(self, setup_name: str) -> None:
        """Load a firefly preset for the given board name ('tf' or 'it_dtc')."""
        try:
            setup_enum = BoardSetup(setup_name.lower())
        except ValueError as exc:
            raise ValueError(f"Unknown board setup '{setup_name}'. Available: "
                             f"{[s.value for s in BoardSetup]}") from exc
        preset = FIREfly_PRESETS[setup_enum]
        # ``preset`` contains ``firefly`` dict and optional ``variant`` dict
        self.firefly_layout = dict(preset)
        self._populate_fireflies_from_layout()

    # ---------------------------------------------------------------------
    # Population helpers
    # ---------------------------------------------------------------------


    def _populate_fireflies_from_layout(self) -> None:
        """Populate firefly devices based on ``self.firefly_layout``.

        Only creates devices for locations explicitly defined in the config.
        Supports types: "4" (4-channel XCVR), "Tx", "Rx", "both" (separate Tx+Rx).
        """
        # firefly_layout now contains both "firefly" and "variant" keys
        firefly_cfg = self.firefly_layout.get("firefly", {})
        variant_map = self.firefly_layout.get("variant", {})
        
        def pick_class(base_cls, key):
            var = variant_map.get(key)
            if var == "cern":
                if base_cls is FireflyTx:
                    return FireflyTxCern
                if base_cls is FireflyRx:
                    return FireflyRxCern
            return base_cls
        
        base = 0x20
        idx = 0
        
        # Only iterate over explicitly configured locations
        for loc, dev_type in firefly_cfg.items():
            if dev_type == "4":
                # 4-channel transceiver
                cls = pick_class(Firefly4, loc)
                self.fireflies[loc] = cls(self.uart, address=base + idx, location=loc)
                idx += 1
            elif dev_type == "Tx":
                # Tx-only
                cls = pick_class(FireflyTx, loc)
                self.fireflies[loc] = cls(self.uart, address=base + idx, location=loc)
                idx += 1
            elif dev_type == "Rx":
                # Rx-only
                cls = pick_class(FireflyRx, loc)
                self.fireflies[loc] = cls(self.uart, address=base + idx, location=loc)
                idx += 1
            elif dev_type == "both":
                # Both Tx and Rx (separate devices, e.g., CERN-B variants)
                tx_key = f"{loc}_Tx"
                rx_key = f"{loc}_Rx"
                tx_cls = pick_class(FireflyTx, tx_key)
                rx_cls = pick_class(FireflyRx, rx_key)
                self.fireflies[tx_key] = tx_cls(self.uart, address=base + idx, location=loc)
                idx += 1
                self.fireflies[rx_key] = rx_cls(self.uart, address=base + idx, location=loc)
                idx += 1
    def _populate_fireflies_generic(self):
        """Populate firefly devices using the original generic mapping.

        Creates four full‑duplex transceiver objects (F1_5, F1_6, F2_5, F2_6)
        and split Tx/Rx objects for the remaining ports.
        """
        """Original generic firefly population (20 devices, 4 transceivers,
        16 split Tx/Rx)."""
        xcvr_locations = {"F1_5", "F1_6", "F2_5", "F2_6"}
        base = 0x20
        idx = 0
        for mod in (1, 2):
            for port in range(1, 7):
                loc = f"F{mod}_{port}"
                if loc in xcvr_locations:
                    self.fireflies[loc] = Firefly4(self.uart,
                                                   address=base + idx,
                                                   location=loc)
                    idx += 1
                else:
                    self.fireflies[f"{loc}_Tx"] = FireflyTx(self.uart,
                                                            address=base + idx,
                                                            location=loc)
                    idx += 1
                    self.fireflies[f"{loc}_Rx"] = FireflyRx(self.uart,
                                                            address=base + idx,
                                                            location=loc)
                    idx += 1

    # Helper look‑ups ---------------------------------------------------
    def get_clock(self, name: str) -> Clock:
        """Return the ``Clock`` instance identified by ``name``.

        Raises ``KeyError`` if the clock does not exist.
        """
        return self.clocks[name]

    def get_firefly(self, loc: str):
        """Return the firefly device (any subclass) for ``loc``.

        ``loc`` matches the keys used in the registry, e.g. ``"F1_2_Tx"``
        or ``"F1_5"`` for a transceiver.
        """
        return self.fireflies[loc]

    def get_lga80d(self, supply_name: str) -> LGA80D:
        return self.lga80d[supply_name]

