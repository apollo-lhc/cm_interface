from enum import IntEnum
from typing import Optional, Tuple
from .base import Device

class FireflyReg(IntEnum):
    """Common registers shared by all firefly variants."""
    STATUS = 0x02
    STATUS_SUMMARY = 0x06
    TEMP_ALARM_LATCH = 0x11
    VCC_ALARM_LATCH = 0x12
    TEMP_MONITOR = 0x16
    VCC_MONITOR = 0x1A
    ELAPSED_TIME = 0x26
    EEPROM_REV = 0x6E
    FIRMWARE_VER = 0x6F
    PAGE_SELECT = 0x7F


class _FireflyBase(Device):
    """Base class handling framing common to all firefly devices."""

    VENDOR_NAME_START: Optional[int] = 0x98
    VENDOR_NAME_LENGTH = 10
    PART_ID_START = 0xAB
    PART_ID_LENGTH = 16
    REVISION_NUMBER_START: Optional[int] = None
    REVISION_NUMBER_LENGTH = 0
    SERIAL_NUMBER_START: Optional[int] = 0xBD
    SERIAL_NUMBER_LENGTH = 10
    EEPROM_REV_START: Optional[int] = 0x6E
    FIRMWARE_VER_START: Optional[int] = 0x6F

    def __init__(self, uart, address: int, location: str):
        super().__init__(uart, address)
        self.location = location
    
    def _encode_command(self, reg: int, write: bool, payload: bytes = b"",
                        read_size: int = 1) -> bytes:
        return self._encode_ascii_command(
            "FF", self.address - 0x20, reg, write, payload, read_size
        )
    
    def _decode_response(self, raw: bytes) -> bytes:
        return self._decode_ascii_response(raw)

    @property
    def temperature(self) -> int:
        """Return the module temperature in signed integer degrees Celsius."""
        raw = self.read_reg(FireflyReg.TEMP_MONITOR)
        return int.from_bytes(raw, "big", signed=True)

    @property
    def status(self) -> int:
        """Return the raw common module status byte."""
        return self.read_reg(FireflyReg.STATUS)[0]

    @property
    def status_summary(self) -> int:
        """Return the raw module fault/alarm summary byte."""
        return self.read_reg(FireflyReg.STATUS_SUMMARY)[0]

    @property
    def supply_voltage(self) -> float:
        """Return module supply voltage in volts."""
        raw = self.read_reg(FireflyReg.VCC_MONITOR, size=2)
        return int.from_bytes(raw, "big") * 100e-6

    @property
    def elapsed_time_hours(self) -> int:
        """Return accumulated operating time in hours."""
        raw = self.read_reg(FireflyReg.ELAPSED_TIME, size=2)
        return int.from_bytes(raw, "big") * 2

    @property
    def firmware_version(self) -> Optional[Tuple[int, int, int]]:
        """Return the module firmware version as ``(major, minor, patch)``.

        Returns ``None`` on variants where this field is reserved.
        """
        if self.FIRMWARE_VER_START is None:
            return None
        return tuple(self.read_reg(self.FIRMWARE_VER_START, size=3))

    @property
    def eeprom_revision(self) -> Optional[int]:
        """Return ``None`` on variants where this field is reserved."""
        if self.EEPROM_REV_START is None:
            return None
        return self.read_reg(self.EEPROM_REV_START)[0]

    @property
    def temperature_alarm(self) -> int:
        return self.read_reg(FireflyReg.TEMP_ALARM_LATCH)[0]

    @property
    def supply_voltage_alarm(self) -> int:
        return self.read_reg(FireflyReg.VCC_ALARM_LATCH)[0]

    @property
    def vendor_name(self) -> Optional[str]:
        """Return ``None`` on variants where this field is reserved."""
        if self.VENDOR_NAME_START is None:
            return None
        return self.read_ascii(self.VENDOR_NAME_START, self.VENDOR_NAME_LENGTH)

    @property
    def part_id(self) -> str:
        return self.read_ascii(self.PART_ID_START, self.PART_ID_LENGTH)

    @property
    def revision_number(self) -> Optional[str]:
        if self.REVISION_NUMBER_START is None:
            return None
        return self.read_ascii(
            self.REVISION_NUMBER_START, self.REVISION_NUMBER_LENGTH
        )

    @property
    def serial_number(self) -> Optional[str]:
        if self.SERIAL_NUMBER_START is None:
            return None
        return self.read_ascii(self.SERIAL_NUMBER_START, self.SERIAL_NUMBER_LENGTH)

    @property
    def vendor(self) -> str:
        """Compatibility alias for :attr:`part_id`."""
        return self.part_id

    @staticmethod
    def _normalize_channels(channels) -> list:
        if channels is None:
            return None
        if isinstance(channels, int):
            return [channels]
        return list(channels)

    @staticmethod
    def _validate_channels(channels: list, max_channel: int) -> None:
        for ch in channels:
            if not isinstance(ch, int) or not 1 <= ch <= max_channel:
                raise ValueError(
                    f"invalid firefly channel: {ch!r} (must be an int 1-{max_channel})"
                )

    def _disable_cdr_12ch(self, base_reg: int, channels=None) -> None:
        """Clear the requested channels' CDR-enable bits on a 12-channel device.

        ``base_reg`` and ``base_reg + 1`` together hold a 12-bit mask: the low
        nibble of ``base_reg`` covers channels 9-12, and ``base_reg + 1``
        covers channels 1-8 (bit0=ch1 .. bit7=ch8). Reserved bits in the high
        nibble of ``base_reg`` are preserved. Every requested channel is
        validated before any UART transaction is issued.
        """
        channels = self._normalize_channels(channels)
        if channels is None:
            channels = list(range(1, 13))
        self._validate_channels(channels, 12)

        hi, lo = self.read_reg(base_reg, size=2)
        mask = ((hi & 0x0F) << 8) | lo
        for ch in channels:
            mask &= ~(1 << (ch - 1))
        new_hi = (hi & 0xF0) | ((mask >> 8) & 0x0F)
        new_lo = mask & 0xFF
        self.write_reg(base_reg, bytes([new_hi, new_lo]))

    def _cdr_status_12ch(self, base_reg: int, channels=None) -> int:
        """Return a 12-bit mask (bit0=ch1 .. bit11=ch12) from a 2-byte status
        register laid out like :meth:`_disable_cdr_12ch`, optionally
        restricted to ``channels``."""
        channels = self._normalize_channels(channels)
        if channels is not None:
            self._validate_channels(channels, 12)

        hi, lo = self.read_reg(base_reg, size=2)
        mask = ((hi & 0x0F) << 8) | lo
        if channels is None:
            return mask
        result = 0
        for ch in channels:
            result |= mask & (1 << (ch - 1))
        return result


class FireflyTx(_FireflyBase):
    """Tx-only firefly (12-channel) – standard variant.

    Register map from ECUO 25G x12 Rev. 4 datasheet.
    """

    # Reserved in the X12 Rev. 4 lower/upper page maps (Tables 28/32).
    VENDOR_NAME_START: Optional[int] = None
    EEPROM_REV_START: Optional[int] = None

    class Reg(IntEnum):
        STATUS = 0x06
        TX_FAULT_LATCH = 0x09
        TEMP_ALARM = 0x11
        VCC_ALARM = 0x12
        TEMP_MONITOR = 0x16
        VCC_MONITOR = 0x1A
        ELAPSED_TIME = 0x26
        RESET = 0x33
        TX_DISABLE = 0x34
        TX_POLARITY = 0x3A
        TX_FAULT_MASK = 0x61
        TEMP_ALARM_MASK = 0x69
        VCC_ALARM_MASK = 0x6A
        # No FIRMWARE_VER/EEPROM_REV here -- reserved for this variant, see
        # VENDOR_NAME_START/EEPROM_REV_START above.
        PAGE_SELECT = 0x7F
        # CDR registers (12-channel Tx). Each is a 2-byte, big-endian field:
        # the base address's low nibble holds channels 9-12, and base+1
        # holds channels 1-8 (bit0=ch1 .. bit7=ch8). See Tables 22/27/45/50.
        CDR_ENABLE_BASE = 0x4A       # Bytes 74-75
        CDR_LOL_STATUS_BASE = 0x14  # Bytes 20-21
        CDR_LOL_MASK_BASE = 0x6C    # Bytes 108-109

    def disable_cdr(self, channels: list = None) -> None:
        """Disable CDR on specified channels (default: all 12).

        Parameters
        ----------
        channels : int or list, optional
            Channel number(s) (1-12). Default: all channels. Invalid
            channels raise ``ValueError`` before any UART transaction.
        """
        self._disable_cdr_12ch(self.Reg.CDR_ENABLE_BASE, channels)

    def get_cdr_status(self, channels: list = None) -> int:
        """Get CDR Loss of Lock status.

        Parameters
        ----------
        channels : int or list, optional
            If given, restrict the returned mask to just these channels.

        Returns
        -------
        int
            12-bit mask where bit0=ch1 .. bit11=ch12; 1 = LOL for that
            channel.
        """
        return self._cdr_status_12ch(self.Reg.CDR_LOL_STATUS_BASE, channels)

    def print_status(self) -> None:
        """Print device status."""
        status = self.read_reg(self.Reg.STATUS)[0] if hasattr(self.Reg, 'STATUS') else 0
        print(f"{self.location} Tx (12-ch): Status=0x{status:02X}, part_id={self.part_id}")


class FireflyTxCern(_FireflyBase):
    """Tx-only firefly – CERN-B variant.
    
    Note: CERN-B devices have a different register map.
    CDR control is NOT available through the standard addresses.
    """
    
    def print_status(self) -> None:
        print(f"{self.location} Tx (CERN-B): part_id={self.part_id}")


class FireflyRx(_FireflyBase):
    """Rx-only firefly (12-channel) – standard variant.

    Register map from ECUO 25G x12 Rev. 4 datasheet.
    """

    # Reserved in the X12 Rev. 4 lower/upper page maps (Tables 28/32).
    VENDOR_NAME_START: Optional[int] = None
    EEPROM_REV_START: Optional[int] = None

    class Reg(IntEnum):
        STATUS = 0x06
        RX_LOS_LATCH = 0x07
        TEMP_ALARM = 0x11
        VCC_ALARM = 0x12
        TEMP_MONITOR = 0x16
        VCC_MONITOR = 0x1A
        ELAPSED_TIME = 0x26
        RESET = 0x33
        RX_DISABLE = 0x34         # Bytes 52-53 (channel disable, Table 40)
        RX_OUTPUT_DISABLE = 0x36  # Bytes 54-55 (output disable, Table 41)
        RX_OUTPUT_AMP = 0x3E
        RX_DEEMPHASIS = 0x44
        RX_LOS_MASK = 0x5F
        # No FIRMWARE_VER/EEPROM_REV here -- reserved for this variant, see
        # VENDOR_NAME_START/EEPROM_REV_START above.
        PAGE_SELECT = 0x7F
        # CDR registers (12-channel Rx). Each is a 2-byte, big-endian field:
        # the base address's low nibble holds channels 9-12, and base+1
        # holds channels 1-8 (bit0=ch1 .. bit7=ch8). See Tables 22/27/45/50.
        CDR_ENABLE_BASE = 0x4A       # Bytes 74-75
        CDR_LOL_STATUS_BASE = 0x14  # Bytes 20-21
        CDR_LOL_MASK_BASE = 0x6C    # Bytes 108-109

    def disable_cdr(self, channels: list = None) -> None:
        """Disable CDR on specified channels (default: all 12).

        Parameters
        ----------
        channels : int or list, optional
            Channel number(s) (1-12). Default: all channels. Invalid
            channels raise ``ValueError`` before any UART transaction.
        """
        self._disable_cdr_12ch(self.Reg.CDR_ENABLE_BASE, channels)

    def get_cdr_status(self, channels: list = None) -> int:
        """Get CDR Loss of Lock status.

        Parameters
        ----------
        channels : int or list, optional
            If given, restrict the returned mask to just these channels.

        Returns
        -------
        int
            12-bit mask where bit0=ch1 .. bit11=ch12; 1 = LOL for that
            channel.
        """
        return self._cdr_status_12ch(self.Reg.CDR_LOL_STATUS_BASE, channels)

    def print_status(self) -> None:
        status = self.read_reg(self.Reg.STATUS)[0] if hasattr(self.Reg, 'STATUS') else 0
        print(f"{self.location} Rx (12-ch): Status=0x{status:02X}, part_id={self.part_id}")


class FireflyRxCern(_FireflyBase):
    """Rx-only firefly – CERN-B variant.
    
    Note: CERN-B devices have a different register map.
    CDR control is NOT available through the standard addresses.
    """
    
    def print_status(self) -> None:
        print(f"{self.location} Rx (CERN-B): part_id={self.part_id}")


class Firefly12(_FireflyBase):
    """12-channel firefly (both Tx & Rx).

    This class has a single UART device number and cannot address the
    separate Tx and Rx register spaces a standard x12 module actually
    exposes (see :class:`FireflyTx`/:class:`FireflyRx`). It previously
    carried a ``disable_cdr()`` method that silently did nothing; that
    method has been removed rather than fixed, since a correct
    implementation would need to route to two underlying devices. Use
    :class:`FireflyTx`/:class:`FireflyRx` directly for CDR control.
    """

    def print_status(self) -> None:
        print(f"{self.location} (12-ch): part_id={self.part_id}")


class Firefly4(_FireflyBase):
    """4-channel firefly (Tx + Rx).

    Register map from ECUO 25G/28G x4 datasheet.
    """

    VENDOR_NAME_START = 0x94
    VENDOR_NAME_LENGTH = 16
    PART_ID_START = 0xA8
    REVISION_NUMBER_START = 0xB8
    REVISION_NUMBER_LENGTH = 2
    # The x4 map differs after its part-number field. Address 0xBD is not an
    # ASCII serial number on the installed B0425040011201 devices. (The x4
    # datasheet places a serial number at a different address, 0xC4/196 --
    # not enabled here pending confirmation against the deployed hardware.)
    SERIAL_NUMBER_START = None
    # Reserved in the x4 lower-page map (Table 27).
    FIRMWARE_VER_START: Optional[int] = None
    EEPROM_REV_START: Optional[int] = None

    class Reg(IntEnum):
        STATUS = 0x02
        TEMP_ALARM = 0x06        # Combined temperature warning/alarm byte
        VCC_ALARM = 0x07         # Combined VCC3.3/VCC1.8 warning/alarm byte
        ELAPSED_TIME = 0x13      # 2 bytes
        TX_DISABLE = 0x56  # Byte 86
        CDR_ENABLE = 0x62  # Byte 98
        CDR_RATE_SELECT = 0x63  # Byte 99
        LOS_MASK = 0x64  # Byte 100
        TX_FAULT_MASK = 0x65  # Byte 101
        CDR_LOL_MASK = 0x66  # Byte 102
        TEMP_MASK = 0x67  # Byte 103
        VCC_MASK = 0x68  # Byte 104
        PAGE_SELECT = 0x7F

    @property
    def status_summary(self) -> int:
        """Not supported on x4: there is no summary byte in this map."""
        raise NotImplementedError(
            "Firefly4 has no STATUS_SUMMARY register; see temperature_alarm/"
            "supply_voltage_alarm instead"
        )

    @property
    def temperature_alarm(self) -> int:
        return self.read_reg(self.Reg.TEMP_ALARM)[0]

    @property
    def supply_voltage_alarm(self) -> int:
        return self.read_reg(self.Reg.VCC_ALARM)[0]

    @property
    def elapsed_time_hours(self) -> int:
        raw = self.read_reg(self.Reg.ELAPSED_TIME, size=2)
        return int.from_bytes(raw, "big") * 2

    def disable_cdr(self, channels: list = None, tx: bool = True, rx: bool = True) -> None:
        """Disable CDR on specified channels.

        CDR Enable byte (0x62) layout:
        Bit 7: Tx4 CDR Enable
        Bit 6: Tx3 CDR Enable
        Bit 5: Tx2 CDR Enable
        Bit 4: Tx1 CDR Enable
        Bit 3: Rx4 CDR Enable
        Bit 2: Rx3 CDR Enable
        Bit 1: Rx2 CDR Enable
        Bit 0: Rx1 CDR Enable

        Parameters
        ----------
        channels : int or list, optional
            Channel number(s) (1-4). Default: all channels. Invalid
            channels raise ``ValueError`` before any UART transaction.
        tx : bool, optional
            Disable CDR on Tx side. Default: True.
        rx : bool, optional
            Disable CDR on Rx side. Default: True.
        """
        channels = self._normalize_channels(channels)
        if channels is None:
            channels = [1, 2, 3, 4]
        self._validate_channels(channels, 4)

        # Read current CDR enable byte
        cdr_enable = self.read_reg(self.Reg.CDR_ENABLE)[0]

        # Clear bits for specified channels
        for ch in channels:
            if tx:
                # Tx channels: bits 4-7 (Tx1=bit4, Tx4=bit7)
                cdr_enable &= ~(1 << (ch + 3))
            if rx:
                # Rx channels: bits 0-3 (Rx1=bit0, Rx4=bit3)
                cdr_enable &= ~(1 << (ch - 1))

        # Write back
        self.write_reg(self.Reg.CDR_ENABLE, bytes([cdr_enable]))

    def get_cdr_status(self, channels: list = None) -> dict:
        """Get CDR enable status for channels.

        Parameters
        ----------
        channels : int or list, optional
            If given, restrict the returned bitmasks to just these
            channels. Default: all four channels.

        Returns
        -------
        dict
            Dictionary with 'tx' and 'rx' bitmasks (bit0=ch1 .. bit3=ch4).
        """
        channels = self._normalize_channels(channels)
        if channels is None:
            channels = [1, 2, 3, 4]
        self._validate_channels(channels, 4)

        cdr_enable = self.read_reg(self.Reg.CDR_ENABLE)[0]

        tx_mask = 0
        rx_mask = 0
        for ch in channels:
            if cdr_enable & (1 << (ch + 3)):
                tx_mask |= 1 << (ch - 1)
            if cdr_enable & (1 << (ch - 1)):
                rx_mask |= 1 << (ch - 1)

        return {
            'tx': tx_mask,
            'rx': rx_mask
        }

    def print_status(self) -> None:
        status = 0
        try:
            status = self.read_reg(self.Reg.STATUS)[0]
        except:
            pass
        
        cdr_status = self.get_cdr_status()
        
        print(f"{self.location} (4-ch): Status=0x{status:02X}, part_id={self.part_id}")
        print(f"  CDR Enable: TX=0b{cdr_status['tx']:04b}, RX=0b{cdr_status['rx']:04b}")


# Backward-compatible alias
Firefly = Firefly12
