import argparse
import contextlib
import datetime
import io
import unittest

from cm_interface.device.mcu import (AlarmReg, McuPage, PowerFlags, PowerFsmState,
                                     PowerReg, RuntimeReg, SystemReg)
from cm_interface.tools import (is_cm_healthy, is_cm_power_on, is_fpga_programmed,
                                is_rtc_set, wait_cm_power_on)
from cm_interface.tools._common import NO, UNKNOWN, YES, run_predicate
from cm_interface.tests.test_mcu_dump import FailingMCU, MemoryMCU


def _mcu(state=PowerFsmState.POWER_ON, raw_done=0b11):
    """raw_done is the active-low wire byte: a set bit means NOT done."""
    mcu = MemoryMCU()
    mcu.put(McuPage.POWER, PowerReg.FSM_STATE, bytes((int(state),)))
    mcu.put(McuPage.RUNTIME, RuntimeReg.FPGA_DONE, bytes((raw_done,)))
    return mcu


class IsCmPowerOnTest(unittest.TestCase):
    def test_on(self):
        self.assertEqual(is_cm_power_on.check(_mcu()), (YES, "yes: POWER_ON"))

    def test_off(self):
        self.assertEqual(is_cm_power_on.check(_mcu(PowerFsmState.POWER_OFF)),
                         (NO, "no: POWER_OFF"))

    def test_unreadable(self):
        self.assertEqual(is_cm_power_on.check(FailingMCU())[0], UNKNOWN)


class IsFpgaProgrammedTest(unittest.TestCase):
    def test_both_done(self):
        self.assertEqual(is_fpga_programmed.check(_mcu(raw_done=0)),
                         (YES, "yes: F1, F2 done"))

    def test_one_not_done(self):
        mcu = _mcu(raw_done=0b10)
        self.assertEqual(is_fpga_programmed.check(mcu), (NO, "no: F2 not done"))
        self.assertEqual(is_fpga_programmed.check(mcu, ("f1",))[0], YES)
        self.assertEqual(is_fpga_programmed.check(mcu, ("f2",))[0], NO)

    def test_power_off_is_no_even_if_pins_read_done(self):
        self.assertEqual(
            is_fpga_programmed.check(_mcu(PowerFsmState.POWER_OFF, raw_done=0)),
            (NO, "no: power is POWER_OFF"))


class WaitCmPowerOnTest(unittest.TestCase):
    def _wait(self, states, timeout=10):
        """Power state sequence, one per poll; fake clock advancing 1 s per sleep."""
        mcu, t, seq = _mcu(), [0.0], list(states)

        def sleep(s):
            t[0] += s
            mcu.put(McuPage.POWER, PowerReg.FSM_STATE, bytes((int(seq.pop(0)),)))
        mcu.put(McuPage.POWER, PowerReg.FSM_STATE, bytes((int(seq.pop(0)),)))
        return wait_cm_power_on.wait(mcu, timeout, 1.0, clock=lambda: t[0], sleep=sleep)

    def test_comes_on(self):
        S = PowerFsmState
        self.assertEqual(self._wait([S.POWER_OFF, S.POWER_L1ON, S.POWER_ON]),
                         (YES, "yes: POWER_ON after 2 s"))

    def test_times_out(self):
        self.assertEqual(self._wait([PowerFsmState.POWER_OFF] * 5, timeout=3),
                         (NO, "no: POWER_OFF after 3 s (timed out)"))

    def test_unreadable_keeps_polling_then_unknown(self):
        sleeps = []
        status, message = wait_cm_power_on.wait(
            FailingMCU(), 2, 1.0, clock=lambda: float(len(sleeps)), sleep=sleeps.append)
        self.assertEqual((status, len(sleeps)), (UNKNOWN, 2))
        self.assertIn("(timed out)", message)


class IsCmHealthyTest(unittest.TestCase):
    def test_healthy_with_warn_latch_noted(self):
        mcu = _mcu()
        mcu.put(McuPage.ALARM, AlarmReg.TEMP_WARN_LATCH, (4).to_bytes(4, "little"))
        self.assertEqual(is_cm_healthy.check(mcu),
                         (YES, "yes: power POWER_ON  (temp warn latch: FPGA)"))

    def test_power_off_is_healthy(self):
        self.assertEqual(is_cm_healthy.check(_mcu(PowerFsmState.POWER_OFF))[0], YES)

    def test_problems(self):
        mcu = _mcu(PowerFsmState.POWER_FAILURE)
        mcu.put(McuPage.SYSTEM, SystemReg.HEALTH_SUMMARY, (1).to_bytes(4, "little"))
        mcu.put(McuPage.POWER, PowerReg.FLAGS,
                bytes((int(PowerFlags.BLADE_POWER_EN | PowerFlags.POWER_FAULT_LATCH),)))
        self.assertEqual(is_cm_healthy.check(mcu),
                         (NO, "no: health POWER_FAULT; latched POWER_FAULT_LATCH; "
                              "power POWER_FAILURE"))


class IsRtcSetTest(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 29, 12, 0, 0)

    def _check(self, valid=1, second=10, tolerance=60):
        mcu = _mcu()
        mcu.put(McuPage.RUNTIME, RuntimeReg.RTC_DATE,
                ((2026 << 16) | (10 << 8) | 29).to_bytes(4, "little"))
        mcu.put(McuPage.RUNTIME, RuntimeReg.RTC_TIME,
                ((valid << 24) | (12 << 16) | second).to_bytes(4, "little"))
        return is_rtc_set.check(mcu, tolerance, now=lambda: self.NOW)

    def test_set(self):
        self.assertEqual(self._check(), (YES, "yes: 2026-10-29T12:00:10 (+10 s vs host)"))

    def test_too_far_off(self):
        self.assertEqual(self._check(tolerance=5)[0], NO)

    def test_invalid(self):
        self.assertEqual(self._check(valid=0), (NO, "no: RTC not valid"))


class RunPredicateTest(unittest.TestCase):
    def test_exception_is_unknown(self):
        def check(mcu):
            raise OSError("no port")
        args = argparse.Namespace(dev_path="/dev/fake", debug=False, quiet=False)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(run_predicate(args, check), UNKNOWN)
        self.assertEqual(err.getvalue(), "unknown: no port\n")
