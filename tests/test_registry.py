import types
import unittest
from unittest.mock import MagicMock

from cm_interface.device.mcu import PowerFsmState
from cm_interface.firefly_presets import BoardSetup, FIREfly_PRESETS
from cm_interface.registry import Registry


TF_WIRE_INDEX = {
    "F2_6": 0,
    "F1_1": 1, "F1_2": 2, "F1_3": 3, "F1_4": 4,
    "F2_3": 5,
}

IT_DTC_WIRE_INDEX = {
    "F1_5": 0, "F1_6": 1, "F2_5": 2, "F2_6": 3,
    "F1_1": 4,
    "F1_2_Tx": 5, "F1_2_Rx": 6,
    "F1_3_Tx": 7, "F1_3_Rx": 8,
    "F1_4_Tx": 9, "F1_4_Rx": 10,
    "F2_2_Tx": 11, "F2_2_Rx": 12,
    "F2_3_Tx": 13, "F2_3_Rx": 14,
    "F2_4_Tx": 15, "F2_4_Rx": 16,
}


class RegistryWireIndexTest(unittest.TestCase):
    def tearDown(self):
        Registry._instance = None

    def _addresses(self, registry):
        return {loc: dev.address for loc, dev in registry.fireflies.items()}

    def test_tf_preset_addresses_match_pinned_wire_index(self):
        registry = Registry(setup="tf")
        expected = {loc: 0x20 + idx for loc, idx in TF_WIRE_INDEX.items()}
        self.assertEqual(self._addresses(registry), expected)

    def test_it_dtc_preset_addresses_match_pinned_wire_index(self):
        registry = Registry(setup="it_dtc")
        expected = {loc: 0x20 + idx for loc, idx in IT_DTC_WIRE_INDEX.items()}
        self.assertEqual(self._addresses(registry), expected)

    def test_reordering_preset_dict_does_not_change_addresses(self):
        """Regression test for R01: wire addresses must not depend on the
        insertion order of the 'firefly' dict, only on 'wire_index'."""
        registry = Registry(setup="tf")
        before = self._addresses(registry)
        Registry._instance = None

        reordered_preset = dict(FIREfly_PRESETS[BoardSetup.TF])
        reordered_preset["firefly"] = dict(
            reversed(list(reordered_preset["firefly"].items()))
        )
        registry = Registry()
        registry.firefly_layout = reordered_preset
        registry.fireflies = {}
        registry._populate_fireflies_from_layout()

        self.assertEqual(self._addresses(registry), before)

    def test_missing_wire_index_entry_raises(self):
        registry = Registry()
        registry.firefly_layout = {
            "firefly": {"F9_9": "Tx"},
            "variant": {},
            "wire_index": {},
        }
        registry.fireflies = {}
        with self.assertRaisesRegex(ValueError, "F9_9"):
            registry._populate_fireflies_from_layout()

    def test_colliding_wire_index_entries_raise(self):
        registry = Registry()
        registry.firefly_layout = {
            "firefly": {"F9_1": "Tx", "F9_2": "Rx"},
            "variant": {},
            "wire_index": {"F9_1": 0, "F9_2": 0},
        }
        registry.fireflies = {}
        with self.assertRaisesRegex(ValueError, "collision"):
            registry._populate_fireflies_from_layout()

    def test_generic_population_helper_removed(self):
        registry = Registry()
        self.assertFalse(hasattr(registry, "_populate_fireflies_generic"))


class ResetAllLga80dSnapshotsTest(unittest.TestCase):
    """Regression tests for Registry.reset_all_lga80d_snapshots.

    Two independent gates, both required: (1) force=True, just to attempt
    anything -- a real hardware write deserves an explicit opt-in; (2) the
    MCU power state machine must read POWER_OFF, checked unconditionally
    whenever force=True and never skippable. The MCU firmware's own
    snapdump_locked() comment says the snapshot-reset command "will fail
    if the device is on", and its LGA80D_init() comment admits the
    firmware itself does not enforce this precondition -- so gate (2)
    exists purely on the Python side."""

    def tearDown(self):
        Registry._instance = None

    def _registry_with_fakes(self, fsm_state):
        registry = Registry()
        registry.mcu = MagicMock()
        registry.mcu.read_power.return_value = types.SimpleNamespace(fsm_state=fsm_state)
        fake_lga_a = MagicMock()
        fake_lga_b = MagicMock()
        registry.lga80d = {"A": fake_lga_a, "B": fake_lga_b}
        return registry, (fake_lga_a, fake_lga_b)

    def test_refuses_without_force_even_when_power_is_off(self):
        registry, (lga_a, lga_b) = self._registry_with_fakes(PowerFsmState.POWER_OFF)

        with self.assertRaisesRegex(RuntimeError, "force=True"):
            registry.reset_all_lga80d_snapshots()

        registry.mcu.read_power.assert_not_called()
        lga_a.reset_all_snapshots.assert_not_called()
        lga_b.reset_all_snapshots.assert_not_called()

    def test_force_alone_does_not_skip_the_power_check(self):
        registry, (lga_a, lga_b) = self._registry_with_fakes(PowerFsmState.POWER_ON)

        with self.assertRaisesRegex(RuntimeError, "POWER_ON"):
            registry.reset_all_lga80d_snapshots(force=True)

        lga_a.reset_all_snapshots.assert_not_called()
        lga_b.reset_all_snapshots.assert_not_called()

    def test_refuses_when_power_is_transitioning(self):
        registry, (lga_a, lga_b) = self._registry_with_fakes(PowerFsmState.POWER_L3ON)

        with self.assertRaises(RuntimeError):
            registry.reset_all_lga80d_snapshots(force=True)

        lga_a.reset_all_snapshots.assert_not_called()

    def test_proceeds_when_forced_and_power_is_off(self):
        registry, (lga_a, lga_b) = self._registry_with_fakes(PowerFsmState.POWER_OFF)

        registry.reset_all_lga80d_snapshots(force=True)

        lga_a.reset_all_snapshots.assert_called_once_with()
        lga_b.reset_all_snapshots.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
