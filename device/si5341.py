"""Si5341 clock generator (R0A on Rev2 boards).

Register map from the Si5341/40 Rev D Family Reference Manual, Rev 1.3
(2021-12-07) chapter 14, cross-checked against Rev 1.1. Differences from the
Si5395 that matter here:

* LOL is 0x000C bit 3 (on the Si5395 that bit is XAXB_ERR, and LOL is
  0x000E bit 1).
* There is no 0x000E/0x000F status, no holdover, no OOF monitor and no
  sticky 0x0013/0x0014.
* 0x000D/0x0012 bits 3:0 are LOS for IN0, IN1, IN2, FB_IN.
* Input selection is 0x0021 (IN_SEL 3 = XA/XB), not 0x052A.
"""
from enum import IntEnum

from .clock import ClockHealth, SiClock

__all__ = ["Si5341Clock", "Si5341Reg", "Si5341StatusBits"]


class Si5341Reg(IntEnum):
    """Si5341 registers used by this package (Si5341/40 RM Rev 1.3, ch. 14)."""
    PAGE = 0x0001
    DEVICE_ID = 0x0002          # PN_BASE, 2 bytes little-endian
    GRADE = 0x0004
    DEVICE_REV = 0x0005
    TEMP_GRADE = 0x0009
    PKG_ID = 0x000A
    I2C_ADDR = 0x000B

    # Status: SYSINCAL, LOSXAXB, LOSREF, LOL, SMBUS_TIMEOUT
    STATUS = 0x000C
    LOS_STATUS = 0x000D         # LOSIN[3:0] = FB_IN, IN2, IN1, IN0

    # Sticky flags
    STATUS_FLAG = 0x0011
    LOS_FLAG = 0x0012

    # Interrupt masks
    INT_MASK_0 = 0x0017
    INT_MASK_1 = 0x0018

    # Reset and control
    SOFT_RESET = 0x001C
    FINC_FDEC = 0x001D
    POWER_DOWN = 0x001E         # bit 0 PDN, bit 1 HARD_RST, bit 2 SYNC
    IN_SEL_REGCTRL = 0x0021     # bit 0 REGCTRL, bits 2:1 IN_SEL

    # LOS configuration
    LOS_ENABLE = 0x002C         # bits 3:0 LOS_EN, bit 4 LOSXAXB_DIS
    LOL_SET_THR = 0x009E

    # NVM
    ACTIVE_NVM_BANK = 0x00E2
    NVM_WRITE = 0x00E3
    NVM_READ_BANK = 0x00E4

    DEVICE_READY = 0x00FE

    # Outputs
    OUTALL_DISABLE = 0x0102
    OUT_PDN_ALL = 0x0145

    # Dividers and reference
    PXAXB = 0x0206
    M_NUM = 0x0235
    M_DEN = 0x023B
    DESIGN_ID = 0x026B
    OPN_ID = 0x0278
    XAXB_EXTCLK_EN = 0x090E
    ZDM_EN = 0x091C
    IN_EN = 0x0949              # bits 3:0 = FB_IN, IN2, IN1, IN0
    IN_TO_PFD_EN = 0x094A       # bits 6:4 = IN2, IN1, IN0

    # Frequency-change preamble/postamble (RM section 7)
    FREQ_CHANGE_PREAMBLE_1 = 0x0B24
    FREQ_CHANGE_PREAMBLE_2 = 0x0B25


class Si5341StatusBits(IntEnum):
    """Bits of 0x000C (and of the sticky copy 0x0011)."""
    SYSINCAL = 0
    LOSXAXB = 1
    LOSREF = 2
    LOL = 3
    SMBUS_TIMEOUT = 5


class Si5341Clock(SiClock):
    """Concrete representation of an Si5341 clock generator.

    Same constructor as :class:`~cm_interface.device.si5395.Si5395Clock`.
    The Si5395-only operations (holdover, input select register 0x052A,
    hitless switching) do not exist here.
    """

    CHIP = "Si5341"
    EXPECTED_PN = 0x5341
    REG = Si5341Reg
    STICKY_FLAG_BYTES = 2

    @property
    def health(self) -> ClockHealth:
        """Live health snapshot for post-startup monitoring.

        Do not call this until :meth:`is_ready` returns ``True`` --
        the registers read here are not part of the startup-safe set.
        """
        self._ensure_identity()
        status, los = self.read_reg(Si5341Reg.STATUS, size=2)
        ready = self.is_ready()
        return ClockHealth(
            system_calibrating=bool(status & (1 << Si5341StatusBits.SYSINCAL)),
            xa_xb_loss_of_signal=bool(status & (1 << Si5341StatusBits.LOSXAXB)),
            xa_xb_error=None,
            ref_loss_of_signal=bool(status & (1 << Si5341StatusBits.LOSREF)),
            smbus_timeout=bool(status & (1 << Si5341StatusBits.SMBUS_TIMEOUT)),
            loss_of_signal=tuple(bool(los & (1 << i)) for i in range(4)),
            out_of_frequency=(),
            loss_of_lock=bool(status & (1 << Si5341StatusBits.LOL)),
            holdover=None,
            pll_calibrating=None,
            ready=ready,
        )

    @property
    def lol_status(self) -> int:
        """Get the status register that holds LOL (0x000C)."""
        return self.read_reg(Si5341Reg.STATUS)[0]

    @property
    def los_status(self) -> int:
        """Get Loss of Signal status for IN0, IN1, IN2, FB_IN."""
        return self.read_reg(Si5341Reg.LOS_STATUS)[0]

    def is_locked(self) -> bool:
        """Check if the PLL is locked to its reference."""
        return not self.lol_status & (1 << Si5341StatusBits.LOL)

    def has_loss_of_signal(self, input_num: int = 0) -> bool:
        """Check LOS on input 0-2, or 3 for FB_IN."""
        if not 0 <= input_num <= 3:
            raise ValueError("input number must be between 0 and 3")
        return bool(self.los_status & (1 << input_num))

    @property
    def sticky_flags(self) -> bytes:
        return self.read_reg(Si5341Reg.STATUS_FLAG, size=2)

    def clear_sticky_flags(self) -> None:
        self.write_reg(Si5341Reg.STATUS_FLAG, bytes(2))
