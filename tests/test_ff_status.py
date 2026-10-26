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


if __name__ == "__main__":
    unittest.main()
