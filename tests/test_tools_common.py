import argparse
import contextlib
import io
import os
import tempfile
import unittest

from cm_interface.tools import _common

NAMES = ["A", "B", "C/D"]


def _select(argv, default_all=False):
    parser = argparse.ArgumentParser()
    _common.add_device_args(parser, "thing", NAMES, default_all=default_all)
    args = parser.parse_args(argv)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            return _common.selected_devices(parser, args, NAMES, default_all=default_all)
        except SystemExit:
            return "error: " + err.getvalue().strip().splitlines()[-1]


class SelectedDevicesTest(unittest.TestCase):
    def test_names_in_given_order_without_duplicates(self):
        self.assertEqual(_select(["C/D", "A", "C/D"]), ["C/D", "A"])

    def test_all(self):
        self.assertEqual(_select(["--all"]), NAMES)

    def test_dump_needs_names_or_all(self):
        self.assertIn("give one or more names, or --all", _select([]))

    def test_check_defaults_to_all(self):
        self.assertEqual(_select([], default_all=True), NAMES)

    def test_names_and_all_conflict(self):
        self.assertIn("not both", _select(["A", "--all"]))

    def test_unknown_name(self):
        self.assertIn("unknown name(s): X", _select(["A", "X"]))


class OutputTest(unittest.TestCase):
    def test_out_path_replaces_slash(self):
        self.assertEqual(_common.out_path("d", 3019, "3V3/1V8_0"),
                         os.path.join("d", "cm3019_3V3-1V8_0.txt"))
        self.assertEqual(_common.out_path("d", None, "mcu"),
                         os.path.join("d", "cmunknown_mcu.txt"))

    def test_write_lines(self):
        with tempfile.TemporaryDirectory() as out_dir:
            path = os.path.join(out_dir, "x.txt")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                _common.write_lines(path, ["a", "b"], failed=True)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "a\nb\n")
        self.assertEqual(out.getvalue(), "wrote %s  (read failures)\n" % path)


class SummaryTest(unittest.TestCase):
    def _summary(self, names, checks, results):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = _common.print_summary(names, checks, results)
        return ok, out.getvalue()

    def test_all_pass(self):
        ok, text = self._summary(["A"], ["x", "y"], {"A": {"x": True, "y": True}})
        self.assertTrue(ok)
        self.assertRegex(text, r"A +OK +OK +OK")
        self.assertTrue(text.endswith("result: OK\n"))

    def test_one_failure_fails_device_and_overall(self):
        ok, text = self._summary(["A", "B"], ["x", "y"],
                                 {"A": {"x": True, "y": False}, "B": {"x": True, "y": True}})
        self.assertFalse(ok)
        self.assertRegex(text, r"A +OK +FAIL +FAIL")
        self.assertRegex(text, r"B +OK +OK +OK")
        self.assertTrue(text.endswith("result: PROBLEMS FOUND\n"))

    def test_device_that_ran_nothing_fails(self):
        ok, text = self._summary(["A"], ["x"], {"A": {}})
        self.assertFalse(ok)
        self.assertRegex(text, r"A +- +FAIL")

    def test_missing_device_fails(self):
        ok, _ = self._summary(["A"], ["x"], {})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
