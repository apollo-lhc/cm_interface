from enum import IntEnum
from typing import Tuple
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

    VENDOR_NAME_START = 0x98
    VENDOR_NAME_LENGTH = 10
    PART_ID_START = 0xAB
    PART_ID_LENGTH = 16
    REVISION_NUMBER_START: int | None = None
    REVISION_NUMBER_LENGTH = 0
    SERIAL_NUMBER_START: int | None = 0xBD
    SERIAL_NUMBER_LENGTH = 10
    
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
    def firmware_version(self) -> Tuple[int, int, int]:
        """Return the module firmware version as ``(major, minor, patch)``."""
        return tuple(self.read_reg(FireflyReg.FIRMWARE_VER, size=3))

    @property
    def eeprom_revision(self) -> int:
        return self.read_reg(FireflyReg.EEPROM_REV)[0]

    @property
    def temperature_alarm(self) -> int:
        return self.read_reg(FireflyReg.TEMP_ALARM_LATCH)[0]

    @property
    def supply_voltage_alarm(self) -> int:
        return self.read_reg(FireflyReg.VCC_ALARM_LATCH)[0]

    @property
    def vendor_name(self) -> str:
        return self.read_ascii(self.VENDOR_NAME_START, self.VENDOR_NAME_LENGTH)

    @property
    def part_id(self) -> str:
        return self.read_ascii(self.PART_ID_START, self.PART_ID_LENGTH)

    @property
    def revision_number(self) -> str | None:
        if self.REVISION_NUMBER_START is None:
            return None
        return self.read_ascii(
            self.REVISION_NUMBER_START, self.REVISION_NUMBER_LENGTH
        )

    @property
    def serial_number(self) -> str | None:
        if self.SERIAL_NUMBER_START is None:
            return None
        return self.read_ascii(self.SERIAL_NUMBER_START, self.SERIAL_NUMBER_LENGTH)

    @property
    def vendor(self) -> str:
        """Compatibility alias for :attr:`part_id`."""
        return self.part_id


class FireflyTx(_FireflyBase):
    """Tx-only firefly (12-channel) – standard variant.
    
    Register map from ECUO 25G x12 datasheet.
    """
    
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
        FIRMWARE_VER = 0x6F
        EEPROM_REV = 0x6E
        PAGE_SELECT = 0x7F
        # CDR registers (12-channel Tx)
        CDR_ENABLE = 0x4B  # Byte 75 = 0x4B
        CDR_LOL_STATUS = 0x15  # Byte 21 = 0x15
        CDR_LOL_MASK = 0x6D  # Byte 109 = 0x6D
    
    def disable_cdr(self, channels: list = None) -> None:
        """Disable CDR on specified channels (default: all 12).
        
        Parameters
        ----------
        channels : list, optional
            Channel numbers (1-12). Default: all channels.
        """
        if channels is None:
            channels = list(range(1, 13))
        
        # Read current CDR enable byte
        cdr_enable = self.read_reg(self.Reg.CDR_ENABLE)[0]
        
        # Clear bits for specified channels (bit 0 = ch1, bit 7 = ch8)
        for ch in channels:
            if 1 <= ch <= 8:
                cdr_enable &= ~(1 << (ch - 1))
        
        # Write back
        self.write_reg(self.Reg.CDR_ENABLE, bytes([cdr_enable]))
        
        # For channels 9-12, need to handle second byte (if applicable)
        # This depends on the specific device variant
    
    def get_cdr_status(self, channels: list = None) -> int:
        """Get CDR Loss of Lock status.
        
        Returns
        -------
        int
            Bitmask where 1 = LOL for that channel
        """
        return self.read_reg(self.Reg.CDR_LOL_STATUS)[0]
    
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
    """Rx-only firefly (12-channel) – standard variant."""
    
    class Reg(IntEnum):
        STATUS = 0x06
        RX_LOS_LATCH = 0x07
        TEMP_ALARM = 0x11
        VCC_ALARM = 0x12
        TEMP_MONITOR = 0x16
        VCC_MONITOR = 0x1A
        ELAPSED_TIME = 0x26
        RESET = 0x33
        RX_DISABLE = 0x36
        RX_OUTPUT_DISABLE = 0x38
        RX_OUTPUT_AMP = 0x3E
        RX_DEEMPHASIS = 0x44
        RX_LOS_MASK = 0x5F
        FIRMWARE_VER = 0x6F
        EEPROM_REV = 0x6E
        PAGE_SELECT = 0x7F
        # CDR registers (12-channel Rx)
        CDR_ENABLE = 0x4B  # Byte 75 = 0x4B
        CDR_LOL_STATUS = 0x15  # Byte 21 = 0x15
        CDR_LOL_MASK = 0x6D  # Byte 109 = 0x6D
    
    def disable_cdr(self, channels: list = None) -> None:
        """Disable CDR on specified channels (default: all 12)."""
        if channels is None:
            channels = list(range(1, 13))
        
        cdr_enable = self.read_reg(self.Reg.CDR_ENABLE)[0]
        
        for ch in channels:
            if 1 <= ch <= 8:
                cdr_enable &= ~(1 << (ch - 1))
        
        self.write_reg(self.Reg.CDR_ENABLE, bytes([cdr_enable]))
    
    def get_cdr_status(self) -> int:
        """Get CDR Loss of Lock status."""
        return self.read_reg(self.Reg.CDR_LOL_STATUS)[0]
    
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
    """12-channel firefly (both Tx & Rx)."""
    
    class Reg(IntEnum):
        TX = 0x10
        RX = 0x20
        # CDR registers
        CDR_TX_ENABLE = 0x4B
        CDR_RX_ENABLE = 0x4B
        CDR_LOL_STATUS = 0x15
    
    def disable_cdr(self, channels: list = None, tx: bool = True, rx: bool = True) -> None:
        """Disable CDR on specified channels.
        
        Parameters
        ----------
        channels : list, optional
            Channel numbers (1-12). Default: all channels.
        tx : bool, optional
            Disable CDR on Tx side. Default: True.
        rx : bool, optional
            Disable CDR on Rx side. Default: True.
        """
        if channels is None:
            channels = list(range(1, 13))
        
        # Note: Full implementation would need separate Tx/Rx enable bytes
        # This is a simplified version
        pass
    
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
    # ASCII serial number on the installed B0425040011201 devices.
    SERIAL_NUMBER_START = None
    
    class Reg(IntEnum):
        STATUS = 0x08
        TX_DISABLE = 0x56  # Byte 86
        CDR_ENABLE = 0x62  # Byte 98
        CDR_RATE_SELECT = 0x63  # Byte 99
        LOS_MASK = 0x64  # Byte 100
        TX_FAULT_MASK = 0x65  # Byte 101
        CDR_LOL_MASK = 0x66  # Byte 102
        TEMP_MASK = 0x67  # Byte 103
        VCC_MASK = 0x68  # Byte 104
        PAGE_SELECT = 0x7F
    
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
        channels : list, optional
            Channel numbers (1-4). Default: all channels.
        tx : bool, optional
            Disable CDR on Tx side. Default: True.
        rx : bool, optional
            Disable CDR on Rx side. Default: True.
        """
        if channels is None:
            channels = [1, 2, 3, 4]
        
        # Read current CDR enable byte
        cdr_enable = self.read_reg(self.Reg.CDR_ENABLE)[0]
        
        # Clear bits for specified channels
        for ch in channels:
            if tx and ch <= 4:
                # Tx channels: bits 4-7 (Tx1=bit4, Tx4=bit7)
                cdr_enable &= ~(1 << (ch + 3))
            if rx and ch <= 4:
                # Rx channels: bits 0-3 (Rx1=bit0, Rx4=bit3)
                cdr_enable &= ~(1 << (ch - 1))
        
        # Write back
        self.write_reg(self.Reg.CDR_ENABLE, bytes([cdr_enable]))
    
    def get_cdr_status(self, channels: list = None) -> dict:
        """Get CDR enable status for channels.
        
        Returns
        -------
        dict
            Dictionary with 'tx' and 'rx' bitmasks
        """
        cdr_enable = self.read_reg(self.Reg.CDR_ENABLE)[0]
        
        tx_mask = (cdr_enable >> 4) & 0x0F
        rx_mask = cdr_enable & 0x0F
        
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
