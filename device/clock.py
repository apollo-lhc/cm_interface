"""Shared base for the Skyworks/Silicon Labs clock chips on the CM.

R0A is an Si5341 on Rev2 boards and an Si5395 on Rev3; R0B-R1C are Si5395
on both. The registers below are at the same address with the same meaning
on both parts (Si5395/94/92 RM Rev 1.3 and Si5341/40 Rev D RM Rev 1.3 and
1.1 agree); everything else lives in the chip subclasses.
"""
from typing import Optional, Tuple

from ..compat import dataclass
from ..errors import ClockIdentityError, ClockNvmWriteRefused
from .base import Device

PN_BASE = 0x0002
GRADE = 0x0004
DEVICE_REV = 0x0005
TEMP_GRADE = 0x0009
PKG_ID = 0x000A
SOFT_RESET = 0x001C
POWER_DOWN = 0x001E      # bit 1 HARD_RST
NVM_WRITE = 0x00E3
NVM_READ_BANK = 0x00E4
DEVICE_READY = 0x00FE
DESIGN_ID = 0x026B
DEVICE_READY_VALUE = 0x0F

# Writing these starts an NVM burn / bank read on the chip. Nothing in this
# package should do that by accident, so SiClock.write_reg refuses them.
_NVM_CONTROL_REGS = frozenset((NVM_WRITE, NVM_READ_BANK))


@dataclass(frozen=True)
class ClockHealth:
    """Decoded live status from a clock chip.

    A field is ``None`` when the chip has no such status bit (the Si5341 has
    no holdover, PLL calibration or XAXB_ERR status, and the Si5395 has no
    LOSREF). ``out_of_frequency`` is empty on the Si5341, which has no OOF
    monitors. ``loss_of_signal`` is per input: IN0-IN3 on the Si5395,
    IN0, IN1, IN2, FB_IN on the Si5341.
    """

    system_calibrating: bool
    xa_xb_loss_of_signal: bool
    xa_xb_error: Optional[bool]
    ref_loss_of_signal: Optional[bool]
    smbus_timeout: bool
    loss_of_signal: Tuple[bool, ...]
    out_of_frequency: Tuple[bool, ...]
    loss_of_lock: bool
    holdover: Optional[bool]
    pll_calibrating: Optional[bool]
    ready: bool

    @property
    def healthy(self) -> bool:
        return not any((
            self.system_calibrating,
            self.xa_xb_loss_of_signal,
            self.xa_xb_error,
            self.ref_loss_of_signal,
            self.smbus_timeout,
            any(self.loss_of_signal),
            any(self.out_of_frequency),
            self.loss_of_lock,
            self.holdover,
            self.pll_calibrating,
        )) and self.ready


class SiClock(Device):
    """Common behavior of the clock chips reached over ProgCom ``CL``.

    Subclasses set ``CHIP``, ``EXPECTED_PN``, ``REG`` (their register
    IntEnum) and implement ``health``. Nothing checks automatically that the
    chip is the expected part; call :meth:`verify_identity` for that.
    """

    CHIP = None
    EXPECTED_PN = None
    REG = None

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

    def verify_identity(self) -> int:
        """Read PN_BASE and raise ``ClockIdentityError`` unless it is ``EXPECTED_PN``.

        Returns the part number. Call only once :meth:`is_ready` is True.
        """
        pn = self.get_device_id()
        if pn != self.EXPECTED_PN:
            raise ClockIdentityError(
                "%s: PN_BASE reads 0x%04X, expected 0x%04X (%s) for this board "
                "revision; check the MCU EEPROM revision (first_mcu / set_id) "
                "and that the firmware was built for this board"
                % (self.name, pn, self.EXPECTED_PN, self.CHIP)
            )
        return pn

    def write_reg(self, reg: int, data: bytes, allow_nvm: bool = False) -> None:
        """Write registers, refusing any write that touches an NVM control register.

        ``NVM_WRITE`` (0x00E3) burns the chip's non-volatile memory and
        ``NVM_READ_BANK`` (0x00E4) loads a bank; a stray write to either is not
        recoverable by this package. A multi-byte write that spans one of them
        is refused too. ``allow_nvm=True`` is the explicit opt-in.
        """
        start = int(reg)
        touched = [r for r in range(start, start + len(data)) if r in _NVM_CONTROL_REGS]
        if touched and not allow_nvm:
            raise ClockNvmWriteRefused(
                "refusing write to %s NVM control register 0x%04X; "
                "pass allow_nvm=True to override" % (self.CHIP, touched[0])
            )
        super().write_reg(reg, data)

    @property
    def health(self) -> ClockHealth:
        raise NotImplementedError

    @property
    def mode(self) -> str:
        """Return a mode inferred from documented live status registers."""
        health = self.health
        if health.system_calibrating or health.pll_calibrating:
            return "calibrating"
        if health.holdover:
            return "holdover"
        if health.loss_of_lock:
            return "unlocked"
        return "locked"

    @property
    def status(self) -> ClockHealth:
        """Compatibility name for the structured live health snapshot."""
        return self.health

    def is_ready(self) -> bool:
        """Return whether the device has finished its startup calibration.

        This reads *only* DEVICE_READY, which is the sole register access
        the manufacturer's startup sequence permits until the device
        reports ready -- callers must not read/write anything else
        beforehand. The device is ready only when this register reads
        exactly ``0x0F``; any other nonzero value is still "not ready",
        not a partial-ready state.
        """
        return self.read_reg(DEVICE_READY)[0] == DEVICE_READY_VALUE

    def get_device_id(self) -> int:
        """Read PN_BASE (0x5395 or 0x5341)."""
        data = self.read_reg(PN_BASE, size=2)
        return int.from_bytes(data, "little")

    @property
    def device_id(self) -> int:
        return self.get_device_id()

    def get_device_rev(self) -> int:
        """Read device revision."""
        return self.read_reg(DEVICE_REV)[0]

    @property
    def device_revision(self) -> int:
        return self.get_device_rev()

    @property
    def grade(self) -> int:
        return self.read_reg(GRADE)[0]

    @property
    def temperature_grade(self) -> int:
        return self.read_reg(TEMP_GRADE)[0]

    @property
    def package_id(self) -> int:
        return self.read_reg(PKG_ID)[0]

    @property
    def design_id(self) -> str:
        return self.read_ascii(DESIGN_ID, 8)

    def reset(self, soft: bool = True) -> None:
        """Perform device reset."""
        if soft:
            self.write_reg(SOFT_RESET, b"\x01")
        else:
            self.write_reg(POWER_DOWN, bytes([0x02]))

    def get_register_name(self, addr: int) -> str:
        """Get symbolic name for a register address."""
        for reg in self.REG:
            if reg.value == addr:
                return reg.name
        return f"0x{addr:04X}"
