"""Si5341 (R0A on Rev2) device class, the shared SiClock identity check, and
Registry's board-revision clock selection.

Status bytes in SI5341_READY are from board 210 (Rev2) R0A, 2026-10-16
(int_test/cm210_R0A.txt), including the undefined bits the chip sets; the
fault cases are built from the Si5341/40 Rev D RM Rev 1.3 chapter 14.
"""
import unittest
from unittest.mock import MagicMock

from cm_interface.core_config import CORE_CONFIG
from cm_interface.device.si5341 import Si5341Clock, Si5341Reg
from cm_interface.device.si5395 import Si5395Clock
from cm_interface.errors import ClockIdentityError, ClockNvmWriteRefused
from cm_interface.registry import Registry


class RegUART:
    """Answers reads from a register dict (missing registers read 0x00)."""

    def __init__(self, regs):
        self.regs = regs
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        fields = self.writes[-1].decode("ascii").split()
        if fields[0] == "w":
            return b"c\n"
        addr = (int(fields[3], 16) << 8) | int(fields[4], 16)
        data = [self.regs.get(addr + k, 0) for k in range(int(fields[5], 16))]
        return ("d " + " ".join("%02X" % b for b in data) + "\n").encode("ascii")

    def read_addrs(self):
        out = []
        for cmd in self.writes:
            f = cmd.decode("ascii").split()
            if f[0] == "r":
                out.append((int(f[3], 16) << 8) | int(f[4], 16))
        return out


SI5341_READY = {0x00FE: 0x0F, 0x0002: 0x41, 0x0003: 0x53,
                0x000C: 0xC0, 0x000D: 0xF0, 0x0011: 0xD0, 0x0012: 0xF0}
SI5395_READY = {0x00FE: 0x0F, 0x0002: 0x95, 0x0003: 0x53}


def _clock(cls, regs, verify=False):
    uart = RegUART(dict(regs))
    return cls(uart, address=0x10, name="R0A", verify_identity=verify), uart


class Si5341HealthTest(unittest.TestCase):
    def test_lol_is_status_bit_3(self):
        clock, _ = _clock(Si5341Clock, SI5341_READY)
        clock.uart.regs[0x000C] = 0xC8
        health = clock.health
        self.assertTrue(health.loss_of_lock)
        self.assertFalse(health.system_calibrating)
        self.assertIsNone(health.xa_xb_error)
        self.assertFalse(health.healthy)

    def test_each_status_bit(self):
        clock, _ = _clock(Si5341Clock, SI5341_READY)
        clock.uart.regs.update({0x000C: 0xE7, 0x000D: 0xF9})
        h = clock.health
        self.assertTrue(h.system_calibrating)
        self.assertTrue(h.xa_xb_loss_of_signal)
        self.assertTrue(h.ref_loss_of_signal)
        self.assertFalse(h.loss_of_lock)
        self.assertTrue(h.smbus_timeout)
        self.assertEqual(h.loss_of_signal, (True, False, False, True))

    def test_captured_board210_status_is_healthy(self):
        clock, _ = _clock(Si5341Clock, SI5341_READY)
        h = clock.health
        self.assertIsNone(h.holdover)
        self.assertIsNone(h.pll_calibrating)
        self.assertEqual(h.out_of_frequency, ())
        self.assertTrue(h.healthy)
        self.assertEqual(clock.mode, "locked")

    def test_health_never_reads_si5395_only_registers(self):
        clock, uart = _clock(Si5341Clock, SI5341_READY)
        clock.health
        self.assertEqual(uart.writes[0], b"r CL 0 0 C 2\n")
        for addr in (0x000E, 0x000F, 0x0013, 0x0014):
            self.assertNotIn(addr, uart.read_addrs())

    def test_is_locked(self):
        clock, _ = _clock(Si5341Clock, {0x000C: 0x00})
        self.assertTrue(clock.is_locked())
        clock, _ = _clock(Si5341Clock, {0x000C: 0x08})
        self.assertFalse(clock.is_locked())
        # Si5395's LOL position (bit 1) is LOSXAXB here, not LOL.
        clock, _ = _clock(Si5341Clock, {0x000C: 0x02})
        self.assertTrue(clock.is_locked())

    def test_sticky_flags_are_two_bytes(self):
        clock, uart = _clock(Si5341Clock, SI5341_READY)
        self.assertEqual(clock.sticky_flags, b"\xD0\xF0")
        clock.clear_sticky_flags()
        self.assertEqual(uart.writes[-1], b"w CL 0 0 11 00 00\n")

    def test_si5395_only_operations_absent(self):
        for attr in ("is_in_holdover", "set_input_select", "enable_hitless_switching"):
            self.assertFalse(hasattr(Si5341Clock, attr), attr)


class Si5341SharedBehaviorTest(unittest.TestCase):
    def test_nvm_guard(self):
        clock, uart = _clock(Si5341Clock, {})
        with self.assertRaises(ClockNvmWriteRefused):
            clock.write_reg(Si5341Reg.NVM_WRITE, b"\xC7")
        self.assertEqual(uart.writes, [])

    def test_reset_wire_format(self):
        clock, uart = _clock(Si5341Clock, {})
        clock.reset(soft=True)
        clock.reset(soft=False)
        self.assertEqual(uart.writes, [b"w CL 0 0 1C 01\n", b"w CL 0 0 1E 02\n"])

    def test_device_id(self):
        clock, _ = _clock(Si5341Clock, SI5341_READY)
        self.assertEqual(clock.device_id, 0x5341)


class IdentityCheckTest(unittest.TestCase):
    def test_mismatch_raises_before_health_read(self):
        clock, uart = _clock(Si5341Clock, SI5395_READY, verify=True)
        with self.assertRaisesRegex(ClockIdentityError, "0x5395.*0x5341"):
            clock.health
        self.assertNotIn(0x000C, uart.read_addrs())

    def test_mismatch_blocks_writes(self):
        clock, uart = _clock(Si5395Clock, SI5341_READY, verify=True)
        with self.assertRaises(ClockIdentityError):
            clock.clear_sticky_flags()
        self.assertFalse(any(w.startswith(b"w ") for w in uart.writes))

    def test_match_is_checked_once(self):
        clock, uart = _clock(Si5341Clock, SI5341_READY, verify=True)
        clock.health
        clock.health
        self.assertEqual(uart.read_addrs().count(0x0002), 1)

    def test_not_ready_skips_the_check(self):
        clock, uart = _clock(Si5341Clock, {0x00FE: 0x00, 0x0002: 0x95, 0x0003: 0x53},
                             verify=True)
        clock.reset()
        self.assertNotIn(0x0002, uart.read_addrs())
        self.assertEqual(uart.writes[-1], b"w CL 0 0 1C 01\n")

    def test_off_by_default(self):
        clock, uart = _clock(Si5341Clock, SI5395_READY)
        clock.health
        self.assertNotIn(0x0002, uart.read_addrs())

    def test_verify_identity_returns_pn(self):
        clock, _ = _clock(Si5395Clock, SI5395_READY)
        self.assertEqual(clock.verify_identity(), 0x5395)


class RegistryRevisionTest(unittest.TestCase):
    def tearDown(self):
        Registry._instance = None

    def _types(self, registry):
        return {name: type(clock) for name, clock in registry.clocks.items()}

    def test_rev2_has_si5341_at_r0a(self):
        registry = Registry(hw_rev=2)
        types = self._types(registry)
        self.assertIs(types.pop("R0A"), Si5341Clock)
        self.assertTrue(all(t is Si5395Clock for t in types.values()))

    def test_rev3_is_all_si5395(self):
        registry = Registry(hw_rev=3)
        self.assertTrue(all(t is Si5395Clock for t in self._types(registry).values()))

    def test_addresses_and_names_unchanged(self):
        for rev in (2, 3):
            Registry._instance = None
            registry = Registry(hw_rev=rev)
            self.assertEqual({n: c.address for n, c in registry.clocks.items()},
                             dict(CORE_CONFIG.clocks))
            self.assertEqual({n: c.name for n, c in registry.clocks.items()},
                             {n: n for n in CORE_CONFIG.clocks})

    def test_registry_clocks_verify_identity(self):
        registry = Registry(hw_rev=2)
        self.assertTrue(all(c._verify for c in registry.clocks.values()))

    def test_unknown_revision_raises(self):
        for rev in (0, 1, 0xFF):
            Registry._instance = None
            registry = Registry(hw_rev=rev)
            with self.assertRaisesRegex(ValueError, "first_mcu"):
                registry.clocks

    def test_revision_read_from_mcu_lazily_and_once(self):
        registry = Registry()
        registry.mcu = MagicMock()
        registry.mcu.hardware_revision.return_value = 2
        registry.mcu.hardware_revision.assert_not_called()
        self.assertIs(type(registry.get_clock("R0A")), Si5341Clock)
        registry.clocks
        registry.mcu.hardware_revision.assert_called_once_with()
        self.assertEqual(registry.hw_rev, 2)

    def test_override_skips_mcu(self):
        registry = Registry(hw_rev=3)
        registry.mcu = MagicMock()
        registry.clocks
        registry.mcu.hardware_revision.assert_not_called()


class McuHardwareRevisionTest(unittest.TestCase):
    def test_cached_u8_read_of_system_offset_6(self):
        from cm_interface.device.mcu import MCU
        uart = RegUART({0x0006: 2})
        mcu = MCU(uart)
        self.assertEqual(mcu.hardware_revision(), 2)
        self.assertEqual(mcu.hardware_revision(), 2)
        self.assertEqual(uart.writes, [b"r MC 0 0 6 1\n"])
        mcu.hardware_revision(refresh=True)
        self.assertEqual(len(uart.writes), 2)


if __name__ == "__main__":
    unittest.main()
