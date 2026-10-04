import operator
import unittest
from typing import Tuple

from cm_interface.compat import (
    _fallback_dataclass,
    _fallback_field,
    spaced_hex,
)
from cm_interface.device.mcu import AdcReading, AdcSnapshot


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

    def test_adc_snapshot_getitem_under_python36_namedtuple_fallback(self):
        # Regression: on Python 3.6/3.7 a namedtuple field is
        # property(itemgetter(i)), i.e. ``self.readings`` calls ``self[0]``.
        # AdcSnapshot.__getitem__ only handled names and read
        # ``self.readings``, so it recursed forever on the target board.
        # Rebuild the class under the fallback decorator with that accessor,
        # reusing the real __getitem__.
        @_fallback_dataclass(frozen=True)
        class Snapshot:
            readings: Tuple[AdcReading, ...]
            __getitem__ = AdcSnapshot.__getitem__

        Snapshot.readings = property(operator.itemgetter(0))
        readings = (AdcReading(0, "A", 1.0), AdcReading(1, "B", float("nan")))
        snapshot = Snapshot(readings)

        self.assertIsInstance(snapshot, tuple)
        self.assertEqual(snapshot.readings, readings)
        self.assertEqual(snapshot["A"], readings[0])
        self.assertFalse(snapshot["B"].valid)
        with self.assertRaises(KeyError):
            snapshot["missing"]

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
