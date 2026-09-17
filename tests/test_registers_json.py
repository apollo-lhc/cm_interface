import glob
import json
import os
import unittest


REGISTERS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "registers")


class RegisterJsonSyntaxTest(unittest.TestCase):
    """These files are documentation only -- nothing in the package loads
    them at runtime -- so the only meaningful automated check is that they
    are syntactically valid JSON (catches hand-edit typos)."""

    def test_all_register_files_are_valid_json(self):
        paths = sorted(glob.glob(os.path.join(REGISTERS_DIR, "*.json")))
        self.assertTrue(paths, "expected at least one registers/*.json file")
        for path in paths:
            with open(path) as f:
                try:
                    json.load(f)
                except json.JSONDecodeError as exc:
                    self.fail(f"{path} is not valid JSON: {exc}")

    def test_deprecated_generic_map_was_removed(self):
        self.assertFalse(
            os.path.exists(os.path.join(REGISTERS_DIR, "firefly.json")),
            "firefly.json was deleted (superseded by firefly12.json/"
            "firefly4.json/firefly_cernb.json, F18) -- it should not reappear",
        )


if __name__ == "__main__":
    unittest.main()
