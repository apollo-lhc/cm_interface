import argparse
import unittest

from cm_interface.tools import ff_status

REV3 = ff_status.SLOTS[3]
# Slots 0/1 = F1_1 Tx/Rx, 9 = F1_6 (x4).
POPULATED = [0, 1, 9]


class SelectSlotsTest(unittest.TestCase):
    def test_all_reads_every_populated_slot_without_notes(self):
        notes, to_read = ff_status.select_slots(REV3, POPULATED, None, 3)
        self.assertEqual(notes, [])
        self.assertEqual(to_read, POPULATED)

    def test_location_selects_both_x12_halves(self):
        notes, to_read = ff_status.select_slots(REV3, POPULATED, ["F1_1"], 3)
        self.assertEqual(notes, [])
        self.assertEqual(to_read, [0, 1])

    def test_named_absent_and_unpopulated_locations_are_reported(self):
        notes, to_read = ff_status.select_slots(REV3, POPULATED, ["F1_7", "F2_6", "F1_6"], 3)
        self.assertEqual(notes, ["F1_7: no such location on board revision 3",
                                 "F2_6: not populated"])
        self.assertEqual(to_read, [9])


class ParseChannelsTest(unittest.TestCase):
    def test_lists_and_ranges(self):
        self.assertEqual(ff_status.parse_channels("5-7,1,6"), [1, 5, 6, 7])

    def test_bad_list_is_an_argparse_error(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            ff_status.parse_channels("1,x")


class FakeInfo:
    hardware_revision = 3
    map_minor = 1
    ff_present_mask = (1 << 0) | (1 << 1) | (1 << 9)
    ff_user_mask = (1 << 0) | (1 << 9)


class FakeRegistry:
    uart = None

    class mcu:
        system_info = FakeInfo()


class FindModulesTest(unittest.TestCase):
    def test_user_mask_and_classes(self):
        _, notes, modules = ff_status.find_modules(FakeRegistry(), ["F1_1", "F1_6"])
        self.assertEqual(notes, ["F1_1 Rx: present but not in user mask; MCU will not access it"])
        self.assertEqual([(n, kind, type(ff).__name__, ff.address, ff.location)
                          for n, _, kind, ff in modules],
                         [(0, "Tx", "FireflyTx", 0x20, "F1_1"),
                          (9, "4", "Firefly4", 0x29, "F1_6")])


if __name__ == "__main__":
    unittest.main()
