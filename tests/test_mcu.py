import contextlib
import io
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
    FpgaDone,
    McuBuildType,
    McuCapability,
    McuControlCommand,
    McuPage,
    McuResetCause,
    PerSupplyState,
    PersistentLogInfoReg,
    PowerFlags,
    PowerFsmState,
    PowerReg,
    RuntimeReg,
    SystemReg,
    TemperatureAlarmBit,
    describe_reset_cause,
)
from cm_interface.errors import (
    CMError,
    McuCapabilityUnavailable,
    McuCoherencyError,
    McuMagicError,
    McuMapVersionError,
    McuProtocolError,
)


class MemoryMCU(MCU):
    def __init__(self):
        self.pages = {page: bytearray(256) for page in McuPage}
        self.reads = []
        self.writes = []
        # Advertise every page by default so tests exercise the readers, not
        # the gate; gating tests overwrite the word explicitly.
        all_caps = (McuCapability.SYSTEM | McuCapability.POWER
                    | McuCapability.ALARMS | McuCapability.ADC
                    | McuCapability.PERSISTENT_LOG | McuCapability.CONTROLS
                    | McuCapability.RUNTIME)
        self.put(McuPage.SYSTEM, SystemReg.CAPABILITIES,
                 int(all_caps).to_bytes(4, "little"))

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


def _put_u32(mcu, page, offset, value):
    mcu.put(page, offset, int(value).to_bytes(4, "little"))


def _system_mcu(minor):
    mcu = MemoryMCU()
    mcu.put(McuPage.SYSTEM, SystemReg.MAGIC, b"CMCU")
    mcu.put(McuPage.SYSTEM, SystemReg.MAP_MAJOR, bytes((1, minor)))
    return mcu


class McuSystemPhase1Test(unittest.TestCase):
    def test_system_info_new_fields_when_minor_1(self):
        mcu = _system_mcu(1)
        _put_u32(mcu, McuPage.SYSTEM, SystemReg.FF_USER_MASK, 0x00FF00FF)
        _put_u32(mcu, McuPage.SYSTEM, SystemReg.FF_PRESENT_MASK, 0x0F0F0F0F)
        mcu.put(McuPage.SYSTEM, SystemReg.BUILD_TYPE, bytes((1,)))
        mcu.put(McuPage.SYSTEM, SystemReg.BUILD_TIME,
                b"12:34:56, Sep 20 2026".ljust(24, b"\0"))

        info = mcu.system_info

        self.assertEqual(info.ff_user_mask, 0x00FF00FF)
        self.assertEqual(info.ff_present_mask, 0x0F0F0F0F)
        self.assertEqual(info.build_type, McuBuildType.DEBUG)
        self.assertEqual(info.build_time, "12:34:56, Sep 20 2026")

    def test_system_info_new_fields_none_when_minor_0(self):
        mcu = _system_mcu(0)

        info = mcu.system_info

        self.assertEqual(
            (info.ff_user_mask, info.ff_present_mask,
             info.build_type, info.build_time),
            (None, None, None, None),
        )
        # Minor-0 firmware answers 0x54+ with "invalid MCU address".
        self.assertFalse([r for r in mcu.reads
                          if r[0] == int(McuPage.SYSTEM) and r[1] >= 0x54])

    def test_firefly_masks_retry_on_change(self):
        mcu = MemoryMCU()
        _put_u32(mcu, McuPage.SYSTEM, SystemReg.FF_USER_MASK, 1)
        _put_u32(mcu, McuPage.SYSTEM, SystemReg.FF_PRESENT_MASK, 2)
        original = mcu.read_reg
        user_reg = (int(McuPage.SYSTEM) << 8) | int(SystemReg.FF_USER_MASK)
        user_reads = {"n": 0}

        def moving_once(reg, size=1):
            value = original(reg, size)
            if int(reg) == user_reg:
                user_reads["n"] += 1
                if user_reads["n"] == 1:   # change right after the first read
                    _put_u32(mcu, McuPage.SYSTEM, SystemReg.FF_USER_MASK, 7)
            return value

        mcu.read_reg = moving_once

        self.assertEqual(mcu._read_firefly_masks(), (7, 2))
        # attempt 1 reads the pair twice and disagrees; attempt 2 confirms
        self.assertEqual(user_reads["n"], 4)

    def test_firefly_masks_raise_when_never_stable(self):
        mcu = MemoryMCU()
        original = mcu.read_reg
        counter = {"n": 0}

        def moving(reg, size=1):
            counter["n"] += 1
            _put_u32(mcu, McuPage.SYSTEM, SystemReg.FF_USER_MASK, counter["n"])
            return original(reg, size)

        mcu.read_reg = moving
        with self.assertRaises(McuCoherencyError):
            mcu._read_firefly_masks()


class McuRuntimeTest(unittest.TestCase):
    def _runtime_mcu(self):
        mcu = MemoryMCU()
        page = McuPage.RUNTIME
        _put_u32(mcu, page, RuntimeReg.HEAP_FREE, 20000)
        _put_u32(mcu, page, RuntimeReg.HEAP_MIN_EVER_FREE, 15000)
        _put_u32(mcu, page, RuntimeReg.HEAP_TOTAL, 25600)
        _put_u32(mcu, page, RuntimeReg.SYSTEM_STACK_UNTOUCHED_WORDS, 90)
        _put_u32(mcu, page, RuntimeReg.SYSTEM_STACK_TOTAL_WORDS, 128)
        mcu.put(page, RuntimeReg.ZYNQMON_TRANSMIT_ENABLED, bytes((1,)))
        mcu.put(page, RuntimeReg.FPGA_DONE, bytes((3,)))
        return mcu

    @staticmethod
    def _set_rtc(mcu, year, month, day, hour, minute, second, valid=1):
        _put_u32(mcu, McuPage.RUNTIME, RuntimeReg.RTC_DATE,
                 (year << 16) | (month << 8) | day)
        _put_u32(mcu, McuPage.RUNTIME, RuntimeReg.RTC_TIME,
                 (valid << 24) | (hour << 16) | (minute << 8) | second)

    def test_runtime_snapshot(self):
        mcu = self._runtime_mcu()
        self._set_rtc(mcu, 2026, 9, 20, 23, 30, 5)

        info = mcu.read_runtime()

        self.assertEqual(
            (info.heap_free_bytes, info.heap_min_ever_free_bytes,
             info.heap_total_bytes, info.system_stack_untouched_words,
             info.system_stack_total_words),
            (20000, 15000, 25600, 90, 128),
        )
        self.assertIs(info.zynqmon_transmit_enabled, True)
        self.assertEqual(info.fpga_done, FpgaDone.F1 | FpgaDone.F2)

    def test_runtime_rtc_valid(self):
        mcu = self._runtime_mcu()
        self._set_rtc(mcu, 2026, 9, 20, 23, 30, 5)

        rtc = mcu.read_runtime().rtc

        self.assertTrue(rtc.valid)
        self.assertEqual(rtc.isoformat(), "2026-09-20T23:30:05")

    def test_runtime_rtc_unset_is_invalid(self):
        mcu = self._runtime_mcu()

        rtc = mcu.read_runtime().rtc

        self.assertFalse(rtc.valid)
        self.assertEqual(rtc.isoformat(), "unset")

    def test_runtime_rtc_retries_on_second_rollover(self):
        mcu = self._runtime_mcu()
        self._set_rtc(mcu, 2026, 9, 20, 23, 30, 5)
        original = mcu.read_reg
        time_reg = (int(McuPage.RUNTIME) << 8) | int(RuntimeReg.RTC_TIME)
        seen = {"n": 0}

        def rolling(reg, size=1):
            value = original(reg, size)
            if int(reg) == time_reg:
                seen["n"] += 1
                if seen["n"] == 1:   # tick right after the first time read
                    self._set_rtc(mcu, 2026, 9, 20, 23, 30, 6)
            return value

        mcu.read_reg = rolling

        self.assertEqual(mcu.read_runtime().rtc.isoformat(), "2026-09-20T23:30:06")

    def test_runtime_rtc_raises_when_never_stable(self):
        mcu = self._runtime_mcu()
        original = mcu.read_reg
        time_reg = (int(McuPage.RUNTIME) << 8) | int(RuntimeReg.RTC_TIME)
        seen = {"n": 0}

        def ticking(reg, size=1):
            value = original(reg, size)
            if int(reg) == time_reg:
                seen["n"] += 1
                self._set_rtc(mcu, 2026, 9, 20, 23, 30, seen["n"] % 60)
            return value

        mcu.read_reg = ticking
        with self.assertRaises(McuCoherencyError):
            mcu.read_runtime()


class McuCapabilityGatingTest(unittest.TestCase):
    def _system_only(self):
        mcu = MemoryMCU()
        _put_u32(mcu, McuPage.SYSTEM, SystemReg.CAPABILITIES, McuCapability.SYSTEM)
        return mcu

    def test_capability_gating_blocks_unadvertised_page(self):
        mcu = self._system_only()

        with self.assertRaises(McuCapabilityUnavailable) as ctx:
            mcu.read_runtime()

        self.assertIsInstance(ctx.exception, CMError)
        self.assertFalse([r for r in mcu.reads if r[0] == int(McuPage.RUNTIME)])

    def test_capability_gating_bypass(self):
        mcu = self._system_only()

        mcu.read_runtime(check_capability=False)

        self.assertTrue([r for r in mcu.reads if r[0] == int(McuPage.RUNTIME)])

    def test_capability_gating_covers_existing_pages(self):
        mcu = self._system_only()
        for call in (mcu.read_adc, mcu.read_power, mcu.read_alarm,
                     mcu.read_persistent_log_info,
                     mcu.read_persistent_log_entries):
            with self.assertRaises(McuCapabilityUnavailable):
                call()
        with self.assertRaises(McuCapabilityUnavailable):
            mcu.send_control(McuControlCommand.CLEAR_POWER_FAULT)
        self.assertEqual(mcu.writes, [])

    def test_system_info_is_never_gated(self):
        mcu = _system_mcu(0)
        _put_u32(mcu, McuPage.SYSTEM, SystemReg.CAPABILITIES, 0)

        mcu.system_info   # must not raise

    def test_capabilities_are_cached_then_refreshed(self):
        mcu = MemoryMCU()
        cap_reg = (int(McuPage.SYSTEM) << 8) | int(SystemReg.CAPABILITIES)

        def cap_reads():
            return len([r for r in mcu.reads
                        if (r[0] << 8 | r[1]) == cap_reg])

        mcu.read_runtime()
        mcu.read_adc()
        self.assertEqual(cap_reads(), 1)
        mcu.capabilities(refresh=True)
        self.assertEqual(cap_reads(), 2)

    def test_zynqmon_control_commands(self):
        mcu = MemoryMCU()

        mcu.send_control(McuControlCommand.ZYNQMON_DISABLE_TRANSMIT)

        self.assertEqual(mcu.writes[-1], (0x7F, 0x00, b"\x06"))
        mcu.send_control(McuControlCommand.ZYNQMON_ENABLE_TRANSMIT)
        self.assertEqual(mcu.writes[-1], (0x7F, 0x00, b"\x05"))


class ReadAsciiTest(unittest.TestCase):
    def test_read_ascii_tolerates_erased_field(self):
        mcu = MemoryMCU()
        mcu.put(McuPage.SYSTEM, SystemReg.GIT_VERSION, b"\xff" * 20)

        self.assertEqual(
            mcu.read_ascii((int(McuPage.SYSTEM) << 8) | int(SystemReg.GIT_VERSION), 20),
            "",
        )


class McuProtocolErrorTest(unittest.TestCase):
    def test_bad_magic_raises_magic_error(self):
        mcu = MemoryMCU()
        mcu.put(McuPage.SYSTEM, SystemReg.MAGIC, b"NOPE")
        with self.assertRaises(McuMagicError) as ctx:
            mcu.system_info
        self.assertIsInstance(ctx.exception, McuProtocolError)
        self.assertIsInstance(ctx.exception, CMError)

    def test_unsupported_major_raises_map_version_error(self):
        mcu = MemoryMCU()
        mcu.put(McuPage.SYSTEM, SystemReg.MAGIC, b"CMCU")
        mcu.put(McuPage.SYSTEM, SystemReg.MAP_MAJOR, bytes((99,)))
        with self.assertRaises(McuMapVersionError) as ctx:
            mcu.system_info
        self.assertIsInstance(ctx.exception, McuProtocolError)

    def test_unstable_power_generation_raises_coherency_error(self):
        mcu = MemoryMCU()
        # An odd generation means a publish is in progress on every attempt.
        mcu.put(McuPage.POWER, PowerReg.GENERATION, (1).to_bytes(4, "little"))
        with self.assertRaises(McuCoherencyError) as ctx:
            mcu.read_power()
        self.assertIsInstance(ctx.exception, McuProtocolError)


class ExerciseMcuTest(unittest.TestCase):
    def test_run_section_degrades_runtime_error_to_a_warning(self):
        from cm_interface import exercise_mcu

        def boom():
            raise RuntimeError("MCU map version mismatch")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exercise_mcu._run_section("System (0x00)", boom)
            exercise_mcu._run_section("after", lambda: print("still ran"))

        self.assertIn("MCU map version mismatch", out.getvalue())
        self.assertIn("still ran", out.getvalue())


if __name__ == "__main__":
    unittest.main()
