import contextlib
import io
import sys
import unittest
from unittest import mock

from cm_interface.tools import lga80d_clear_faults


class FakeLGA80D:
    def __init__(self, name, log, fail):
        self.name, self.log, self.fail = name, log, fail

    def clear_faults(self):
        if self.name in self.fail:
            raise OSError("no ack")
        self.log.append(self.name)

    def read_status_word(self, page):
        return 0x0840 if page else 0x0040


class FakeRegistry:
    def __init__(self, fail=()):
        self.cleared, self.fail = [], set(fail)

    def get_lga80d(self, name):
        return FakeLGA80D(name, self.cleared, self.fail)


def _main(reg, argv):
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["x"] + argv), \
            mock.patch.object(lga80d_clear_faults, "make_registry", lambda args: reg), \
            contextlib.redirect_stdout(out):
        return lga80d_clear_faults.main(), out.getvalue()


class ClearFaultsToolTest(unittest.TestCase):
    def test_clears_and_reads_back_status(self):
        reg = FakeRegistry()
        status, out = _main(reg, ["F1VCCINT1", "--yes"])
        self.assertEqual((status, reg.cleared), (0, ["F1VCCINT1"]))
        self.assertIn("page 0 STATUS_WORD 0x0040, page 1 STATUS_WORD 0x0840", out)

    def test_names_and_failure_reporting(self):
        reg = FakeRegistry(fail=["F1VCCINT2"])
        status, out = _main(reg, ["F1VCCINT1", "F1VCCINT2", "F2VCCINT1", "--yes"])
        self.assertEqual((status, reg.cleared), (1, ["F1VCCINT1", "F2VCCINT1"]))
        self.assertIn("F1VCCINT2: FAILED: no ack", out)

    def test_aborts_without_confirmation(self):
        reg = FakeRegistry()
        with mock.patch("builtins.input", return_value="n"):
            status, out = _main(reg, ["--all"])
        self.assertEqual((status, reg.cleared), (1, []))
        self.assertIn("Aborted.", out)

    def test_needs_names_or_all(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _main(FakeRegistry(), [])


if __name__ == "__main__":
    unittest.main()
