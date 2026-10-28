import unittest

from cm_interface.device.firefly import (
    Firefly4,
    Firefly12,
    FireflyRx,
    FireflyRxCern,
    FireflyTx,
    FireflyTxCern,
)


class FakeUART:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        return self.responses.pop(0)


class FireflyRxTest(unittest.TestCase):
    def test_disable_and_output_disable_addresses(self):
        uart = FakeUART(b"c\n", b"c\n")
        rx = FireflyRx(uart, address=0x21, location="F1_1")

        rx.write_reg(rx.Reg.RX_DISABLE, b"\x00\x00")
        # ProgCom FF writes take one data byte, so two transactions.
        self.assertEqual(uart.writes, [b"w FF 1 0 34 00\n", b"w FF 1 0 35 00\n"])

        uart2 = FakeUART(b"c\n", b"c\n")
        rx2 = FireflyRx(uart2, address=0x21, location="F1_1")
        rx2.write_reg(rx2.Reg.RX_OUTPUT_DISABLE, b"\x00\x00")
        self.assertEqual(uart2.writes, [b"w FF 1 0 36 00\n", b"w FF 1 0 37 00\n"])

    def test_disable_cdr_all_channels_preserves_reserved_bits(self):
        # hi=0xFA (reserved nibble 1111, ch9-12 bits: ch9=0,ch10=1,ch11=0,ch12=1)
        # lo=0xFF (ch1-8 all enabled)
        uart = FakeUART(b"d FA FF\n", b"c\n", b"c\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")

        rx.disable_cdr()

        self.assertEqual(uart.writes, [
            b"r FF 0 0 4A 2\n",
            b"w FF 0 0 4A F0\n",
            b"w FF 0 0 4B 00\n",
        ])

    def test_disable_cdr_partial_mask(self):
        uart = FakeUART(b"d FA FF\n", b"c\n", b"c\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")

        rx.disable_cdr(channels=[3, 10])

        self.assertEqual(uart.writes, [
            b"r FF 0 0 4A 2\n",
            b"w FF 0 0 4A F8\n",
            b"w FF 0 0 4B FB\n",
        ])

    def test_disable_cdr_accepts_single_int_channel(self):
        uart = FakeUART(b"d F0 FF\n", b"c\n", b"c\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")

        rx.disable_cdr(channels=1)

        self.assertEqual(uart.writes, [
            b"r FF 0 0 4A 2\n",
            b"w FF 0 0 4A F0\n",
            b"w FF 0 0 4B FE\n",
        ])

    def test_disable_cdr_reaches_channels_9_through_12(self):
        # All 12 enabled: hi=0xFF (ch9-12 all set), lo=0xFF
        uart = FakeUART(b"d FF FF\n", b"c\n", b"c\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")

        rx.disable_cdr(channels=[9, 10, 11, 12])

        # ch9..12 = mask bits 8..11, all cleared -> hi low nibble = 0
        self.assertEqual(uart.writes, [
            b"r FF 0 0 4A 2\n",
            b"w FF 0 0 4A F0\n",
            b"w FF 0 0 4B FF\n",
        ])

    def test_disable_cdr_rejects_invalid_channels_with_zero_writes(self):
        for bad in (0, 13, -1, "3", 1.5):
            uart = FakeUART()
            rx = FireflyRx(uart, address=0x20, location="F1_1")
            with self.assertRaises(ValueError):
                rx.disable_cdr(channels=[bad])
            self.assertEqual(uart.writes, [], f"channel {bad!r} issued UART traffic")

    def test_get_cdr_status_full_mask(self):
        uart = FakeUART(b"d 0A FF\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")

        status = rx.get_cdr_status()

        self.assertEqual(status, 0x0AFF)
        self.assertEqual(uart.writes, [b"r FF 0 0 14 2\n"])

    def test_get_cdr_status_channel_filter(self):
        uart = FakeUART(b"d 0A FF\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")

        # 0x0AFF = 0000 1010 1111 1111 -> ch1(bit0)=1, ch10(bit9)=1
        status = rx.get_cdr_status(channels=[1, 10])

        self.assertEqual(status, (1 << 0) | (1 << 9))

    def test_enable_cdr_sets_bits_and_preserves_reserved(self):
        uart = FakeUART(b"d F0 00\n", b"c\n", b"c\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")

        rx.enable_cdr(channels=[1, 12])

        self.assertEqual(uart.writes, [
            b"r FF 0 0 4A 2\n",
            b"w FF 0 0 4A F8\n",
            b"w FF 0 0 4B 01\n",
        ])

    def test_get_cdr_enable_reads_enable_register(self):
        # ff_cdr_ena on hardware prints 0xff0f: byte 0x4A=0x0F, 0x4B=0xFF.
        uart = FakeUART(b"d 0F FF\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")
        self.assertEqual(rx.get_cdr_enable(), 0xFFF)
        self.assertEqual(uart.writes, [b"r FF 0 0 4A 2\n"])

    def test_output_amplitude_nibble_order(self):
        # Table 43: 0x3E high nibble = ch12 ... 0x43 low nibble = ch1.
        uart = FakeUART(b"d C1 23 45 67\n", b"d 89 AB\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")

        values = rx.get_output_amplitude()

        self.assertEqual(values[12], 0xC)
        self.assertEqual(values[11], 0x1)
        self.assertEqual(values[2], 0xA)
        self.assertEqual(values[1], 0xB)
        self.assertEqual(uart.writes, [b"r FF 0 0 3E 4\n", b"r FF 0 0 42 2\n"])

    def test_set_output_preemphasis_writes_only_changed_bytes(self):
        uart = FakeUART(b"d 22 22 22 22\n", b"d 22 22\n", b"c\n", b"c\n")
        rx = FireflyRx(uart, address=0x20, location="F1_1")

        rx.set_output_preemphasis(5, channels=[1, 12])

        self.assertEqual(uart.writes[2:], [
            b"w FF 0 0 44 52\n",
            b"w FF 0 0 49 25\n",
        ])

    def test_set_output_amplitude_rejects_bad_value_before_writing(self):
        uart = FakeUART()
        rx = FireflyRx(uart, address=0x20, location="F1_1")
        with self.assertRaises(ValueError):
            rx.set_output_amplitude(16)
        self.assertEqual(uart.writes, [])

    def test_capability_guards_return_none(self):
        rx = FireflyRx(FakeUART(), address=0x20, location="F1_1")
        self.assertIsNone(rx.vendor_name)
        self.assertIsNone(rx.eeprom_revision)


class FireflyTxTest(unittest.TestCase):
    def test_disable_cdr_shares_rx_bug_fix(self):
        uart = FakeUART(b"d FA FF\n", b"c\n", b"c\n")
        tx = FireflyTx(uart, address=0x20, location="F2_3")

        tx.disable_cdr()

        self.assertEqual(uart.writes, [
            b"r FF 0 0 4A 2\n",
            b"w FF 0 0 4A F0\n",
            b"w FF 0 0 4B 00\n",
        ])

    def test_capability_guards_return_none(self):
        tx = FireflyTx(FakeUART(), address=0x20, location="F2_3")
        self.assertIsNone(tx.vendor_name)
        self.assertIsNone(tx.eeprom_revision)


class FireflyCernTest(unittest.TestCase):
    def test_cern_variants_still_expose_base_capabilities(self):
        tx = FireflyTxCern(FakeUART(), address=0x20, location="F1_2")
        rx = FireflyRxCern(FakeUART(), address=0x21, location="F1_2")
        self.assertEqual(tx.VENDOR_NAME_START, 0x98)
        self.assertEqual(rx.EEPROM_REV_START, 0x6E)

    def test_cern_variants_have_no_cdr_methods(self):
        tx = FireflyTxCern(FakeUART(), address=0x20, location="F1_2")
        rx = FireflyRxCern(FakeUART(), address=0x21, location="F1_2")
        self.assertFalse(hasattr(tx, "disable_cdr"))
        self.assertFalse(hasattr(rx, "disable_cdr"))


class Firefly12Test(unittest.TestCase):
    def test_disable_cdr_no_longer_exists(self):
        f = Firefly12(FakeUART(), address=0x20, location="F2_6")
        self.assertFalse(hasattr(f, "disable_cdr"))


class Firefly4Test(unittest.TestCase):
    def test_status_register_address(self):
        self.assertEqual(int(Firefly4.Reg.STATUS), 0x02)

    def test_status_summary_raises(self):
        f = Firefly4(FakeUART(), address=0x20, location="F1_5")
        with self.assertRaises(NotImplementedError):
            f.status_summary

    def test_temperature_and_voltage_alarm_addresses(self):
        uart = FakeUART(b"d 01\n")
        f = Firefly4(uart, address=0x20, location="F1_5")
        self.assertEqual(f.temperature_alarm, 1)
        self.assertEqual(uart.writes, [b"r FF 0 0 6 1\n"])

        uart2 = FakeUART(b"d 02\n")
        f2 = Firefly4(uart2, address=0x20, location="F1_5")
        self.assertEqual(f2.supply_voltage_alarm, 2)
        self.assertEqual(uart2.writes, [b"r FF 0 0 7 1\n"])

    def test_elapsed_time_hours_address(self):
        uart = FakeUART(b"d 00 05\n")
        f = Firefly4(uart, address=0x20, location="F1_5")
        self.assertEqual(f.elapsed_time_hours, 10)
        self.assertEqual(uart.writes, [b"r FF 0 0 13 2\n"])

    def test_firmware_and_eeprom_unsupported(self):
        f = Firefly4(FakeUART(), address=0x20, location="F1_5")
        self.assertIsNone(f.firmware_version)
        self.assertIsNone(f.eeprom_revision)

    def test_disable_cdr_rejects_channel_0_with_zero_writes(self):
        uart = FakeUART()
        f = Firefly4(uart, address=0x20, location="F1_5")
        with self.assertRaises(ValueError):
            f.disable_cdr(channels=[0])
        self.assertEqual(uart.writes, [])

    def test_disable_cdr_rejects_channel_5_with_zero_writes(self):
        uart = FakeUART()
        f = Firefly4(uart, address=0x20, location="F1_5")
        with self.assertRaises(ValueError):
            f.disable_cdr(channels=[5])
        self.assertEqual(uart.writes, [])

    def test_disable_cdr_channels_1_to_4_round_trip(self):
        uart = FakeUART(b"d FF\n", b"c\n")
        f = Firefly4(uart, address=0x20, location="F1_5")

        f.disable_cdr(channels=[1, 4], tx=True, rx=False)

        # Tx1=bit4, Tx4=bit7 cleared; Rx untouched since rx=False
        self.assertEqual(uart.writes, [
            b"r FF 0 0 62 1\n",
            b"w FF 0 0 62 6F\n",
        ])

    def test_enable_cdr_rx_only(self):
        uart = FakeUART(b"d 00\n", b"c\n")
        f = Firefly4(uart, address=0x20, location="F1_5")

        f.enable_cdr(channels=[2], tx=False, rx=True)

        self.assertEqual(uart.writes, [b"r FF 0 0 62 1\n", b"w FF 0 0 62 02\n"])

    def test_output_amplitude_uses_upper_page_3(self):
        # Table 49: 0xEE high nibble = Rx1 ... 0xEF low nibble = Rx4. The page
        # goes in the ProgCom command, not a separate PAGE_SELECT write.
        uart = FakeUART(b"d 12 30\n")
        f = Firefly4(uart, address=0x33, location="F2_6")

        self.assertEqual(f.get_output_amplitude(), {1: 1, 2: 2, 3: 3, 4: 0})
        self.assertEqual(uart.writes, [b"r FF 13 3 EE 2\n"])

    def test_set_output_preemphasis_upper_page_3(self):
        uart = FakeUART(b"d 00 00\n", b"c\n")
        f = Firefly4(uart, address=0x20, location="F1_5")

        f.set_output_preemphasis(7, channels=[3])

        self.assertEqual(uart.writes, [b"r FF 0 3 EC 2\n", b"w FF 0 3 ED 70\n"])

    def test_reserved_codes_rejected_before_any_traffic(self):
        uart = FakeUART()
        f = Firefly4(uart, address=0x20, location="F1_5")
        with self.assertRaises(ValueError):
            f.set_output_amplitude(4)
        with self.assertRaises(ValueError):
            f.set_output_preemphasis(8)
        self.assertEqual(uart.writes, [])

    def test_get_cdr_status_filters_and_validates(self):
        uart = FakeUART(b"d FF\n")
        f = Firefly4(uart, address=0x20, location="F1_5")

        status = f.get_cdr_status(channels=1)

        self.assertEqual(status, {"tx": 1, "rx": 1})

        uart2 = FakeUART()
        f2 = Firefly4(uart2, address=0x20, location="F1_5")
        with self.assertRaises(ValueError):
            f2.get_cdr_status(channels=[0])
        self.assertEqual(uart2.writes, [])


if __name__ == "__main__":
    unittest.main()
