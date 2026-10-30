import contextlib
import io
import sys
import types
import unittest
from unittest import mock

from cm_interface.device.mcu import PowerFsmState
from cm_interface.tools import lga80d_snapshot_reset


class FakeRegistry:
    def __init__(self, state, fail=()):
        self.state, self.fail, self.reset = state, set(fail), []

    def get_mcu(self):
        return self

    def read_power(self):
        return types.SimpleNamespace(fsm_state=self.state, flags=0)

    def reset_all_lga80d_snapshots(self, force=False, names=None):
        assert force
        if names[0] in self.fail:
            raise OSError("no ack")
        self.reset.extend(names)


def _main(reg, argv):
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["x"] + argv), \
            mock.patch.object(lga80d_snapshot_reset, "make_registry", lambda args: reg), \
            contextlib.redirect_stdout(out):
        return lga80d_snapshot_reset.main(), out.getvalue()


class SnapshotResetTest(unittest.TestCase):
    def test_refuses_unless_power_off(self):
        reg = FakeRegistry(PowerFsmState.POWER_ON)
        status, out = _main(reg, ["--all", "--yes"])
        self.assertEqual((status, reg.reset), (1, []))
        self.assertIn("refusing", out)

    def test_names_and_failure_reporting(self):
        reg = FakeRegistry(PowerFsmState.POWER_OFF, fail=["F1VCCINT2"])
        status, out = _main(reg, ["F1VCCINT1", "F1VCCINT2", "F2VCCINT1", "--yes"])
        self.assertEqual((status, reg.reset), (1, ["F1VCCINT1", "F2VCCINT1"]))
        self.assertIn("F1VCCINT2: FAILED: no ack", out)

    def test_aborts_without_confirmation(self):
        reg = FakeRegistry(PowerFsmState.POWER_OFF)
        with mock.patch("builtins.input", return_value="n"):
            status, out = _main(reg, ["--all"])
        self.assertEqual((status, reg.reset), (1, []))
        self.assertIn("Aborted.", out)

    def test_needs_names_or_all(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _main(FakeRegistry(PowerFsmState.POWER_OFF), [])


if __name__ == "__main__":
    unittest.main()
