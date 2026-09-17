import unittest

from cm_interface.device.si5395 import Clock, Si5395Reg


class FakeUART:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        return self.responses.pop(0)


class DeviceReadyTest(unittest.TestCase):
    def test_exactly_0x0f_is_ready(self):
        uart = FakeUART(b"d 0F\n")
        clock = Clock(uart, address=0x10, name="R0A")
        self.assertTrue(clock.is_ready())

    def test_other_nonzero_values_are_not_ready(self):
        for value in (b"d 01\n", b"d 02\n", b"d FF\n", b"d 08\n"):
            uart = FakeUART(value)
            clock = Clock(uart, address=0x10, name="R0A")
            self.assertFalse(clock.is_ready(), f"{value!r} should not be ready")

    def test_zero_is_not_ready(self):
        uart = FakeUART(b"d 00\n")
        clock = Clock(uart, address=0x10, name="R0A")
        self.assertFalse(clock.is_ready())

    def test_is_ready_touches_only_device_ready_register(self):
        uart = FakeUART(b"d 0F\n")
        clock = Clock(uart, address=0x10, name="R0A")
        clock.is_ready()
        self.assertEqual(len(uart.writes), 1)
        self.assertIn(b"FE", uart.writes[0])  # DEVICE_READY = 0x00FE

    def test_health_ready_field_uses_exact_match(self):
        uart = FakeUART(b"d 00 00 00 00\n", b"d 01\n")
        clock = Clock(uart, address=0x10, name="R0A")
        self.assertFalse(clock.health.ready)


class RenamedRegistersTest(unittest.TestCase):
    def test_nvm_write_unchanged(self):
        self.assertEqual(int(Si5395Reg.NVM_WRITE), 0x00E3)

    def test_freq_change_preamble_names_no_longer_mention_nvm(self):
        names = [reg.name for reg in Si5395Reg]
        self.assertIn("FREQ_CHANGE_PREAMBLE_1", names)
        self.assertIn("FREQ_CHANGE_PREAMBLE_2", names)
        for name in names:
            if name.startswith("FREQ_CHANGE_PREAMBLE"):
                self.assertNotIn("NVM", name)


class RegressionLocksTest(unittest.TestCase):
    def test_reset_soft_wire_format(self):
        uart = FakeUART(b"c\n")
        clock = Clock(uart, address=0x10, name="R0A")
        clock.reset(soft=True)
        self.assertEqual(uart.writes, [b"w CL 0 0 1C 01\n"])

    def test_reset_hard_wire_format(self):
        uart = FakeUART(b"c\n")
        clock = Clock(uart, address=0x10, name="R0A")
        clock.reset(soft=False)
        self.assertEqual(uart.writes, [b"w CL 0 0 1E 02\n"])

    def test_is_locked_bit_decoding(self):
        uart = FakeUART(b"d 00\n")
        clock = Clock(uart, address=0x10, name="R0A")
        self.assertTrue(clock.is_locked())

        uart2 = FakeUART(b"d 02\n")
        clock2 = Clock(uart2, address=0x10, name="R0A")
        self.assertFalse(clock2.is_locked())


if __name__ == "__main__":
    unittest.main()
