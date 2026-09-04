import unittest

from cm_interface.device.fpga import FPGA
from cm_interface.errors import FPGAInterfaceUnavailable, RegisterAccessError
from cm_interface.registry import Registry


class FakeUART:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        return self.responses.pop(0)


class FPGATest(unittest.TestCase):
    def tearDown(self):
        Registry._instance = None

    def test_f1_read_preserves_wire_byte_order(self):
        uart = FakeUART(b"d 78 56 34 12\n")
        fpga = FPGA(uart, 0)

        self.assertEqual(fpga.read_reg(0x20, size=4), b"\x78\x56\x34\x12")
        self.assertEqual(uart.writes, [b"r FP 0 0 20 4\n"])

    def test_f2_byte_write_preserves_wire_byte_order(self):
        uart = FakeUART(b"c\n")
        fpga = FPGA(uart, 1)

        fpga.write_reg(0x04, b"\x12\x34\x56\x78")
        self.assertEqual(uart.writes, [b"w FP 1 0 4 12 34 56 78\n"])

    def test_integer_write_requires_size_and_is_little_endian(self):
        uart = FakeUART(b"c\n")
        fpga = FPGA(uart, 0)

        fpga.write_reg(0x00, 0x12345678, size=4)
        self.assertEqual(uart.writes, [b"w FP 0 0 0 78 56 34 12\n"])

        with self.assertRaisesRegex(ValueError, "size is required"):
            fpga.write_reg(0x00, 1)

    def test_rejects_nonzero_page_and_crossing_byte_address_space(self):
        fpga = FPGA(FakeUART(), 0)

        with self.assertRaisesRegex(ValueError, "out of range"):
            fpga.read_reg(0x100)
        with self.assertRaisesRegex(ValueError, "beyond register 0xFF"):
            fpga.read_reg(0xFE, size=4)
        with self.assertRaisesRegex(ValueError, "beyond register 0xFF"):
            fpga.read_block(0xFC, 5)

    def test_address_ack_error_has_specific_exception(self):
        fpga = FPGA(FakeUART(b"e read failed: ADDR_ACK_ERROR\n"), 1)

        with self.assertRaises(FPGAInterfaceUnavailable):
            fpga.read_reg(0)

    def test_data_ack_error_remains_generic(self):
        fpga = FPGA(FakeUART(b"e read failed: DATA_ACK_ERROR\n"), 0)

        with self.assertRaises(RegisterAccessError) as context:
            fpga.read_reg(0)
        self.assertNotIsInstance(context.exception, FPGAInterfaceUnavailable)

    def test_ambiguous_nack_text_remains_generic(self):
        fpga = FPGA(FakeUART(b"e I2C NACK\n"), 0)

        with self.assertRaises(RegisterAccessError) as context:
            fpga.read_reg(0)
        self.assertNotIsInstance(context.exception, FPGAInterfaceUnavailable)

    def test_other_transport_error_remains_generic(self):
        fpga = FPGA(FakeUART(b"e I2C timeout\n"), 0)

        with self.assertRaises(RegisterAccessError) as context:
            fpga.read_reg(0)
        self.assertNotIsInstance(context.exception, FPGAInterfaceUnavailable)

    def test_probe_reports_expected_unavailability(self):
        fpga = FPGA(FakeUART(b"e read failed: ADDR_ACK_ERROR\n"), 0)

        result = fpga.probe()
        self.assertFalse(result)
        self.assertIn("unavailable", result.reason)

    def test_registry_exposes_both_fpga_positions(self):
        registry = Registry()

        self.assertIs(registry.get_fpga("F1"), registry.fpgas["F1"])
        self.assertIs(registry.get_fpga("F2"), registry.fpgas["F2"])
        self.assertEqual(registry.fpgas["F1"].fpga_number, 0)
        self.assertEqual(registry.fpgas["F2"].fpga_number, 1)


if __name__ == "__main__":
    unittest.main()
