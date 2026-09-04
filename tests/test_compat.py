import unittest

from cm_interface.compat import (
    _fallback_dataclass,
    _fallback_field,
    spaced_hex,
)


class CompatibilityTest(unittest.TestCase):
    def test_python36_frozen_record_fallback(self):
        @_fallback_dataclass(frozen=True)
        class Record:
            value: int
            label: str = "default"

            @property
            def doubled(self):
                return self.value * 2

        record = Record(3)

        self.assertEqual(record.label, "default")
        self.assertEqual(record.doubled, 6)
        with self.assertRaises(AttributeError):
            record.value = 4

    def test_spaced_hex_does_not_require_bytes_separator_support(self):
        self.assertEqual(spaced_hex(b"\x00\xab\xff"), "00 ab ff")

    def test_python36_mutable_record_and_default_factory(self):
        @_fallback_dataclass
        class Record:
            name: str
            values: list = _fallback_field(default_factory=list)

        first = Record("first")
        second = Record(name="second")
        first.values.append(1)

        self.assertEqual(first.values, [1])
        self.assertEqual(second.values, [])
        first.name = "changed"
        self.assertEqual(first.name, "changed")


if __name__ == "__main__":
    unittest.main()
