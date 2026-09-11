import math
import struct
import unittest

from cm_interface.device.mcu import (
    ADC_CHANNEL_COUNT,
    MCU,
    AdcReg,
    AlarmReg,
    AlarmTaskState,
    ControlReg,
    McuCapability,
    McuControlCommand,
    McuPage,
    McuResetCause,
    PerSupplyState,
    PersistentLogInfoReg,
    PowerFlags,
    PowerFsmState,
    PowerReg,
    SystemReg,
    TemperatureAlarmBit,
    describe_reset_cause,
)


class MemoryMCU(MCU):
    def __init__(self):
        self.pages = {page: bytearray(256) for page in McuPage}
        self.reads = []
        self.writes = []

    def put(self, page, offset, data):
        self.pages[int(page)][int(offset):int(offset) + len(data)] = data

    def read_reg(self, reg, size=1):
        page, offset = divmod(int(reg), 256)
        self.reads.append((page, offset, size))
        return bytes(self.pages[page][offset:offset + size])

    def write_reg(self, reg, data):
        page, offset = divmod(int(reg), 256)
        self.writes.append((page, offset, bytes(data)))
        self.pages[page][offset:offset + len(data)] = data


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
        reset_cause = McuResetCause.POR | McuResetCause.SW
        mcu.put(McuPage.SYSTEM, SystemReg.RESET_CAUSE,
                int(reset_cause).to_bytes(4, "little"))
        mcu.put(McuPage.SYSTEM, SystemReg.GIT_VERSION, b"v1.2.3\0" + bytes(13))

        info = mcu.system_info

        self.assertEqual((info.map_major, info.map_minor), (1, 2))
        self.assertEqual(info.hardware_revision, 3)
        self.assertEqual(info.board_id, 5186)
        self.assertEqual(info.reset_cause, reset_cause)
        self.assertEqual(
            describe_reset_cause(info.reset_cause),
            ("POR: power-on reset", "SW: software-requested system reset"),
        )
        self.assertEqual(info.git_version, "v1.2.3")
        self.assertTrue(info.capabilities & McuCapability.ADC)

    def test_reset_cause_reports_unknown_bits(self):
        cause = McuResetCause(int(McuResetCause.WDT0) | (1 << 7))
        self.assertEqual(
            describe_reset_cause(cause),
            (
                "WDT0: Watchdog Timer 0 timed out",
                "UNKNOWN: reserved/unrecognized bits 0x00000080",
            ),
        )

    def test_adc_binary16_and_nan(self):
        mcu = MemoryMCU()
        page = McuPage.ADC
        values = [float(i) / 4 for i in range(ADC_CHANNEL_COUNT)]
        values[3] = float("nan")
        mcu.put(page, AdcReg.VALUES,
                b"".join(struct.pack("<e", value) for value in values))

        snapshot = mcu.read_adc()

        self.assertTrue(math.isclose(snapshot["VCC_1V8"].value, 1.0))
        self.assertTrue(snapshot["VCC_1V8"].valid)
        self.assertFalse(snapshot["VCC_4V0"].valid)
        self.assertEqual(snapshot["TM4C_TEMP"].index, 20)

    def test_power_snapshot(self):
        mcu = MemoryMCU()
        page = McuPage.POWER
        mcu.put(page, PowerReg.GENERATION, (4).to_bytes(4, "little"))
        mcu.put(page, PowerReg.FSM_STATE, bytes((int(PowerFsmState.POWER_ON),)))
        mcu.put(page, PowerReg.FLAGS,
                bytes((int(PowerFlags.BLADE_POWER_EN | PowerFlags.F1_ENABLE),)))
        mcu.put(page, PowerReg.LIVE_PG_MASK, (0xFFF).to_bytes(4, "little"))
        mcu.put(page, PowerReg.EXPECTED_PG_MASK, (0xFFF).to_bytes(4, "little"))
        mcu.put(page, PowerReg.SUPPLY_COUNT, bytes((12,)))
        states = [int(PerSupplyState.PWR_ON)] * 11 + [int(PerSupplyState.PWR_FAILED)]
        mcu.put(page, PowerReg.SUPPLY_STATE, bytes(states))

        snapshot = mcu.read_power()

        self.assertEqual(snapshot.generation, 4)
        self.assertEqual(snapshot.fsm_state, PowerFsmState.POWER_ON)
        self.assertTrue(snapshot.flags & PowerFlags.F1_ENABLE)
        self.assertEqual(snapshot.supply_count, 12)
        self.assertEqual(snapshot.supply_states[-1], PerSupplyState.PWR_FAILED)

    def test_alarm_snapshot(self):
        mcu = MemoryMCU()
        page = McuPage.ALARM
        mcu.put(page, AlarmReg.TEMP_TASK_STATE,
                bytes((int(AlarmTaskState.ALM_WARN),)))
        mcu.put(page, AlarmReg.VOLTAGE_TASK_STATE,
                bytes((int(AlarmTaskState.ALM_NORMAL),)))
        mcu.put(page, AlarmReg.TEMP_STATUS,
                int(TemperatureAlarmBit.FPGA).to_bytes(4, "little"))
        mcu.put(page, AlarmReg.TEMP_WARN_LATCH,
                int(TemperatureAlarmBit.FPGA | TemperatureAlarmBit.DCDC).to_bytes(4, "little"))
        mcu.put(page, AlarmReg.VOLTAGE_ALARM_GENERAL, bytes((0x01,)))
        mcu.put(page, AlarmReg.VOLTAGE_ALARM_F1, bytes((0x02,)))
        mcu.put(page, AlarmReg.VOLTAGE_ALARM_F2, bytes((0x00,)))

        snapshot = mcu.read_alarm()

        self.assertEqual(snapshot.temp_task_state, AlarmTaskState.ALM_WARN)
        self.assertEqual(snapshot.temp_status, TemperatureAlarmBit.FPGA)
        self.assertTrue(snapshot.temp_warn_latch & TemperatureAlarmBit.DCDC)
        self.assertEqual(snapshot.voltage_alarm_general, 0x01)
        self.assertEqual(snapshot.voltage_alarm_f1, 0x02)

    def test_persistent_log_info(self):
        mcu = MemoryMCU()
        page = McuPage.PERSISTENT_LOG_INFO
        mcu.put(page, PersistentLogInfoReg.FORMAT_VERSION, (1).to_bytes(4, "little"))
        mcu.put(page, PersistentLogInfoReg.CAPACITY_WORDS, (64).to_bytes(4, "little"))
        mcu.put(page, PersistentLogInfoReg.GENERATION, (7).to_bytes(4, "little"))

        info = mcu.read_persistent_log_info()

        self.assertEqual(info.format_version, 1)
        self.assertEqual(info.capacity_words, 64)
        self.assertEqual(info.generation, 7)

    def test_persistent_log_entries(self):
        mcu = MemoryMCU()
        page = McuPage.PERSISTENT_LOG_DATA
        mcu.put(page, 0x00, (0xDEADBEEF).to_bytes(4, "little"))

        entries = mcu.read_persistent_log_entries()

        self.assertEqual(len(entries), 64)
        self.assertEqual(entries[0], 0xDEADBEEF)

    def test_send_control(self):
        mcu = MemoryMCU()
        mcu.send_control(McuControlCommand.CLEAR_ALARM_LATCHES)

        self.assertEqual(
            mcu.writes[-1],
            (int(McuPage.CONTROL), int(ControlReg.COMMAND), bytes((4,))),
        )


if __name__ == "__main__":
    unittest.main()
