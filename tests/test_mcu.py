import math
import struct
import unittest

from cm_interface.device.mcu import (
    ADC_CHANNEL_COUNT,
    MCU,
    AdcReg,
    AdcTargetReg,
    McuCapability,
    McuPage,
    SystemReg,
)


class MemoryMCU(MCU):
    def __init__(self):
        self.pages = {page: bytearray(256) for page in McuPage}
        self.reads = []

    def put(self, page, offset, data):
        self.pages[int(page)][int(offset):int(offset) + len(data)] = data

    def read_reg(self, reg, size=1):
        page, offset = divmod(int(reg), 256)
        self.reads.append((page, offset, size))
        return bytes(self.pages[page][offset:offset + size])


class McuTest(unittest.TestCase):
    def test_command_encoding(self):
        mcu = MCU(None)
        self.assertEqual(
            mcu._encode_command(0x0320, False, read_size=4),
            b"r MC 0 3 20 4\n",
        )

    def test_system_info(self):
        mcu = MemoryMCU()
        mcu.put(McuPage.SYSTEM, SystemReg.MAGIC, b"CMCU")
        mcu.put(McuPage.SYSTEM, SystemReg.MAP_MAJOR, bytes((1, 2, 3, 21)))
        mcu.put(McuPage.SYSTEM, SystemReg.CAPABILITIES,
                int(McuCapability.SYSTEM | McuCapability.ADC).to_bytes(4, "little"))
        mcu.put(McuPage.SYSTEM, SystemReg.BOARD_ID, (5186).to_bytes(4, "little"))
        mcu.put(McuPage.SYSTEM, SystemReg.UPTIME_SECONDS, (123).to_bytes(4, "little"))
        mcu.put(McuPage.SYSTEM, SystemReg.GIT_VERSION, b"v1.2.3\0" + bytes(13))

        info = mcu.system_info

        self.assertEqual((info.map_major, info.map_minor), (1, 2))
        self.assertEqual(info.hardware_revision, 3)
        self.assertEqual(info.board_id, 5186)
        self.assertEqual(info.git_version, "v1.2.3")
        self.assertTrue(info.capabilities & McuCapability.ADC)

    def test_adc_binary16_and_bitmaps(self):
        mcu = MemoryMCU()
        page = McuPage.ADC
        mcu.put(page, AdcReg.GENERATION, (8).to_bytes(4, "little"))
        mcu.put(page, AdcReg.SAMPLE_UPTIME_SECONDS, (99).to_bytes(4, "little"))
        mcu.put(page, AdcReg.VALID_CHANNELS, ((1 << 21) - 1).to_bytes(4, "little"))
        mcu.put(page, AdcReg.ERROR_CHANNELS, (1 << 3).to_bytes(4, "little"))
        mcu.put(page, AdcReg.CHANNEL_COUNT, bytes((ADC_CHANNEL_COUNT,)))
        values = [float(i) / 4 for i in range(ADC_CHANNEL_COUNT)]
        mcu.put(page, AdcReg.VALUES,
                b"".join(struct.pack("<e", value) for value in values))

        snapshot = mcu.read_adc()

        self.assertEqual(snapshot.generation, 8)
        self.assertTrue(math.isclose(snapshot["VCC_4V0"].value, 0.75))
        self.assertTrue(snapshot["VCC_4V0"].error)
        self.assertEqual(snapshot["TM4C_TEMP"].index, 20)

    def test_adc_targets_preserve_channel_indexing(self):
        mcu = MemoryMCU()
        page = McuPage.ADC_TARGET
        valid = (1 << 13) - 1
        mcu.put(page, AdcTargetReg.GENERATION, (2).to_bytes(4, "little"))
        mcu.put(page, AdcTargetReg.VALID_CHANNELS, valid.to_bytes(4, "little"))
        mcu.put(page, AdcTargetReg.CHANNEL_COUNT, bytes((ADC_CHANNEL_COUNT,)))
        mcu.put(page, AdcTargetReg.VALUES,
                b"".join(struct.pack("<e", 1.0) for _ in range(ADC_CHANNEL_COUNT)))

        targets = mcu.read_adc_targets()

        self.assertTrue(targets["F2_VCCAUX"].valid)
        self.assertFalse(targets["CUR_V_12V"].valid)


if __name__ == "__main__":
    unittest.main()
