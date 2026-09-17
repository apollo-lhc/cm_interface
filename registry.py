from .uart import UART
from .device.si5395 import Clock
from .device.firefly import Firefly12, Firefly4, FireflyTx, FireflyRx, FireflyTxCern, FireflyRxCern, Firefly
from .device.lga80d import LGA80D
from .device.mcu import MCU, PowerFsmState
from .device.fpga import FPGA
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

    def __new__(cls, setup: str = None, debug: Optional[TextIO] = None,
                dev_path: str = "/dev/ttyUL4",
                timeout: float = UART.DEFAULT_TIMEOUT):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init(setup, debug, dev_path, timeout)
        return cls._instance

    def _init(self, setup: str = None, debug: Optional[TextIO] = None,
              dev_path: str = "/dev/ttyUL4",
              timeout: float = UART.DEFAULT_TIMEOUT):
        self.uart = UART(debug=debug, dev_path=dev_path, timeout=timeout)
        self.mcu = MCU(self.uart)
        self.fpgas = {
            "F1": FPGA(self.uart, fpga_number=0, name="F1"),
            "F2": FPGA(self.uart, fpga_number=1, name="F2"),
        }
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

        Each location's UART device index is taken from the preset's explicit
        ``wire_index`` map (not from dict-iteration order), so reordering or
        editing ``firefly_cfg`` cannot silently shift another location's wire
        address.
        """
        # firefly_layout now contains "firefly", "variant" and "wire_index" keys
        firefly_cfg = self.firefly_layout.get("firefly", {})
        variant_map = self.firefly_layout.get("variant", {})
        wire_index = self.firefly_layout.get("wire_index", {})

        def pick_class(base_cls, key):
            var = variant_map.get(key)
            if var == "cern":
                if base_cls is FireflyTx:
                    return FireflyTxCern
                if base_cls is FireflyRx:
                    return FireflyRxCern
            return base_cls

        def resolve_index(key):
            try:
                return wire_index[key]
            except KeyError:
                raise ValueError(
                    f"preset location {key!r} has no pinned wire_index entry"
                ) from None

        base = 0x20
        seen_indices = {}

        def claim_index(key, idx):
            if idx in seen_indices:
                raise ValueError(
                    f"wire_index collision: {key!r} and {seen_indices[idx]!r} "
                    f"both claim index {idx}"
                )
            seen_indices[idx] = key

        # Only iterate over explicitly configured locations
        for loc, dev_type in firefly_cfg.items():
            if dev_type == "4":
                # 4-channel transceiver
                idx = resolve_index(loc)
                claim_index(loc, idx)
                cls = pick_class(Firefly4, loc)
                self.fireflies[loc] = cls(self.uart, address=base + idx, location=loc)
            elif dev_type == "Tx":
                # Tx-only
                idx = resolve_index(loc)
                claim_index(loc, idx)
                cls = pick_class(FireflyTx, loc)
                self.fireflies[loc] = cls(self.uart, address=base + idx, location=loc)
            elif dev_type == "Rx":
                # Rx-only
                idx = resolve_index(loc)
                claim_index(loc, idx)
                cls = pick_class(FireflyRx, loc)
                self.fireflies[loc] = cls(self.uart, address=base + idx, location=loc)
            elif dev_type == "both":
                # Both Tx and Rx (separate devices, e.g., CERN-B variants)
                tx_key = f"{loc}_Tx"
                rx_key = f"{loc}_Rx"
                tx_idx = resolve_index(tx_key)
                rx_idx = resolve_index(rx_key)
                claim_index(tx_key, tx_idx)
                claim_index(rx_key, rx_idx)
                tx_cls = pick_class(FireflyTx, tx_key)
                rx_cls = pick_class(FireflyRx, rx_key)
                self.fireflies[tx_key] = tx_cls(self.uart, address=base + tx_idx, location=loc)
                self.fireflies[rx_key] = rx_cls(self.uart, address=base + rx_idx, location=loc)

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

    def get_mcu(self) -> MCU:
        """Return the command-module MCU device."""
        return self.mcu

    def get_fpga(self, name: str) -> FPGA:
        """Return the raw generic-interface object for ``F1`` or ``F2``."""
        return self.fpgas[name]

    # ---------------------------------------------------------------------
    # LGA80D snapshot reset
    # ---------------------------------------------------------------------
    def reset_all_lga80d_snapshots(self, force: bool = False) -> None:
        """Reset the snapshot history registers on every configured LGA80D.

        Modeled on the MCU firmware's ``sn_all`` command
        (commands/PowerCommands.c), which loops over every configured
        supply and both PMBus pages, capturing and then resetting each
        one's snapshot register.

        The snapshot-reset command only succeeds while a supply's output
        is off (confirmed by the firmware's own comment on this exact
        sequence in ``snapdump_locked()``: "This will fail if the device
        is on"). The firmware itself does not enforce this precondition
        -- its ``LGA80D_init()`` comment notes "these settings need to be
        called when the supply output is OFF... this is currently not
        ensured in this code."

        This method refuses to run at all unless ``force=True`` is
        passed -- a real hardware write with a hardware precondition
        deserves an explicit opt-in. Whenever it does run (``force=True``),
        it unconditionally reads the MCU's board-level power state machine
        (:class:`~cm_interface.device.mcu.PowerFsmState`) and refuses to
        proceed unless it reads exactly ``POWER_OFF`` -- ``force`` cannot
        skip that check; it only unlocks the attempt.

        Parameters
        ----------
        force : bool, optional
            Required to attempt this at all. Does not bypass the
            power-state check.

        Raises
        ------
        RuntimeError
            If ``force`` is not set, or if the power state machine is not
            in ``PowerFsmState.POWER_OFF``.
        """
        if not force:
            raise RuntimeError(
                "refusing to reset LGA80D snapshots without force=True: "
                "this is a real hardware write that only succeeds while "
                "every supply's output is off. Pass force=True to "
                "proceed -- this will still check the MCU power state "
                "machine reports POWER_OFF before doing anything, and "
                "refuse otherwise."
            )

        fsm_state = self.mcu.read_power().fsm_state
        if fsm_state != PowerFsmState.POWER_OFF:
            raise RuntimeError(
                "refusing to reset LGA80D snapshots: MCU power state "
                f"machine reports {fsm_state.name}, not POWER_OFF -- "
                "the snapshot-reset command only succeeds while a "
                "supply's output is off."
            )

        for lga in self.lga80d.values():
            lga.reset_all_snapshots()
