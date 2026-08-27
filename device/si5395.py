from enum import IntEnum
from .base import Device
from ..utils import pack_uint16, unpack_uint16

class Si5395Reg(IntEnum):
    """Register map for Si5395 clock generator (Page 0x00 registers).
    
    Based on Si5395-94-92 Family Reference Manual.
    Full register map includes multiple pages (0x00-0x0C).
    """
    # Page 0x00 - Device Status, Interrupts, Resets
    PAGE = 0x0001
    DEVICE_ID = 0x0002
    DEVICE_REV = 0x0005
    TEMP_GRADE = 0x0009
    PKG_ID = 0x000A
    I2C_ADDR = 0x000B
    
    # Status registers
    MODE = 0x0000
    SYSINCAL = 0x000C
    LOS_STATUS = 0x000D
    LOL_HOLD_STATUS = 0x000E
    CAL_PLL_STATUS = 0x000F
    
    # Flag registers (sticky)
    SYSINCAL_FLAG = 0x0011
    LOS_OOF_FLAG = 0x0012
    LOL_HOLD_FLAG = 0x0013
    CAL_FLAG = 0x0014
    
    # Interrupt masks
    INT_MASK_0 = 0x0017
    INT_MASK_1 = 0x0018
    INT_MASK_2 = 0x0019
    INT_MASK_3 = 0x001A
    
    # Reset and control
    SOFT_RESET = 0x001C
    FINC_FDEC = 0x001D
    POWER_DOWN = 0x001E
    
    # LOS (Loss of Signal) configuration
    LOS_ENABLE = 0x002C
    LOS_TRG_THR_IN0 = 0x002E
    LOS_CLR_THR_IN0 = 0x0036
    
    # OOF (Out of Frequency) configuration
    OOF_ENABLE = 0x003F
    OOF_REF_SEL = 0x0040
    
    # LOL (Loss of Lock) configuration
    LOL_FAST_EN = 0x0092
    LOL_SLOW_EN = 0x009A
    
    # NVM (Non-Volatile Memory)
    ACTIVE_NVM_BANK = 0x00E2
    NVM_WRITE = 0x00E3
    NVM_READ_BANK = 0x00E4
    
    # Device ready status
    DEVICE_READY = 0x00FE
    
    # Page 0x01 - Output Configuration
    OUTALL_DISABLE = 0x0102
    OUT0A_CONFIG = 0x0103
    OUT0A_FORMAT = 0x0104
    OUT0A_MUX = 0x0106
    OUT_PDN_ALL = 0x0145
    
    # Page 0x02 - Input Dividers
    PXAXB = 0x0206
    P0_NUM = 0x0208
    P0_DEN = 0x020E
    P_UPDATE = 0x0230
    
    # Page 0x05 - DSPLL Configuration
    BW_PLL = 0x0508
    FASTLOCK_BW_PLL = 0x050E
    BW_UPDATE_PLL = 0x0514
    CLK_SWITCH_MODE = 0x0536
    IN_SEL_REGCTRL = 0x052A
    ZDM_EN = 0x0487
    HSW_EN = 0x0536
    
    # Page 0x0B - NVM Control
    NVM_CTRL = 0x0B24
    NVM_STATUS = 0x0B25


class LolStatusBits(IntEnum):
    """LOL (Loss of Lock) status bit definitions."""
    LOL = 1
    HOLD = 5


class LosStatusBits(IntEnum):
    """LOS (Loss of Signal) status bit definitions."""
    LOS_IN0 = 0
    LOS_IN1 = 1
    LOS_IN2 = 2
    LOS_IN3 = 3
    OOF_IN0 = 4
    OOF_IN1 = 5
    OOF_IN2 = 6
    OOF_IN3 = 7


class Clock(Device):
    """Concrete representation of an Si5395 clock generator.

    Parameters
    ----------
    uart : UART
        Shared UART instance used for communication.
    address : int
        Device address on the serial bus (e.g., 0x10 for R0A).
    name : str
        Logical identifier (e.g., "R0A", "R1B").
    
    Examples
    --------
    >>> clock = Clock(uart, address=0x10, name="R0A")
    >>> # Using symbolic register names (recommended)
    >>> status = clock.read_reg(Si5395Reg.LOL_HOLD_STATUS)
    >>> # Using convenience properties
    >>> mode = clock.mode
    >>> clock.mode = 0x01  # Set to locked mode
    >>> # Check specific status bits
    >>> if clock.is_locked():
    ...     print("PLL is locked")
    """
    def __init__(self, uart, address: int, name: str):
        super().__init__(uart, address)
        self.name = name

    def _encode_command(self, reg: int, write: bool, payload: bytes = b"",
                        read_size: int = 1) -> bytes:
        return self._encode_ascii_command(
            "CL", self.address - 0x10, reg, write, payload, read_size
        )

    def _decode_response(self, raw: bytes) -> bytes:
        return self._decode_ascii_response(raw)

    @property
    def mode(self) -> int:
        """Get current clock mode (0=free-run, 1=locked, 2=holdover)."""
        return self.read_reg(Si5395Reg.MODE)[0]

    @mode.setter
    def mode(self, value: int) -> None:
        self.write_reg(Si5395Reg.MODE, pack_uint16(value))

    @property
    def status(self) -> int:
        """Get device status register."""
        return self.read_reg(Si5395Reg.STATUS)[0]

    @property
    def lol_status(self) -> int:
        """Get Loss of Lock status register."""
        return self.read_reg(Si5395Reg.LOL_HOLD_STATUS)[0]

    @property
    def los_status(self) -> int:
        """Get Loss of Signal status for all inputs."""
        return self.read_reg(Si5395Reg.LOS_STATUS)[0]

    def is_locked(self) -> bool:
        """Check if PLL is locked to input clock."""
        status = self.lol_status
        return (status & (1 << LolStatusBits.LOL)) == 0

    def has_loss_of_signal(self, input_num: int = 0) -> bool:
        """Check if specified input has loss of signal."""
        status = self.los_status
        return bool(status & (1 << input_num))

    def is_in_holdover(self) -> bool:
        """Check if device is in holdover mode."""
        status = self.lol_status
        return bool(status & (1 << LolStatusBits.HOLD))

    def get_device_id(self) -> int:
        """Read device ID register (should be 0x5395)."""
        data = self.read_reg(Si5395Reg.DEVICE_ID, size=2)
        return int.from_bytes(data, "little")

    def get_device_rev(self) -> int:
        """Read device revision."""
        return self.read_reg(Si5395Reg.DEVICE_REV)[0]

    def reset(self, soft: bool = True) -> None:
        """Perform device reset."""
        if soft:
            self.write_reg(Si5395Reg.SOFT_RESET, pack_uint16(0x01))
        else:
            self.write_reg(Si5395Reg.POWER_DOWN, bytes([0x02]))

    def set_input_select(self, input_num: int) -> None:
        """Manually select input clock (0-3)."""
        self.write_reg(Si5395Reg.IN_SEL_REGCTRL, bytes([input_num & 0x03]))

    def enable_hitless_switching(self, enable: bool = True) -> None:
        """Enable or disable hitless input switching."""
        val = 0x04 if enable else 0x00
        self.write_reg(Si5395Reg.HSW_EN, bytes([val]))

    def get_register_name(self, addr: int) -> str:
        """Get symbolic name for a register address."""
        for reg in Si5395Reg:
            if reg.value == addr:
                return reg.name
        return f"0x{addr:04X}"
