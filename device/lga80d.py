import time
from enum import IntEnum
from typing import List, Optional

from ..compat import dataclass
from ..errors import RegisterAccessError
from .base import Device
from ..utils import decode_linear11, decode_linear16u

# Time to wait after triggering a snapshot capture (SNAPSHOT_CONTROL=0x01)
# before the device has copied its live registers into the readable snapshot
# buffer. Modeled on the MCU firmware's snapdump_locked() in LocalTasks.c,
# which found ~20ms insufficient (returned a zero-length block read) and
# settled on twice that.
SNAPSHOT_CAPTURE_DELAY_S = 0.04

# The SNAPSHOT register (PMBus 0xEA) is 32 bytes. The MCU serves it through the
# ProgCom "SN" device: one capture write, then cached reads of up to 4 bytes.
SNAPSHOT_BYTES = 32
_SNAPSHOT_READ_BYTES = 4


@dataclass(frozen=True)
class LGA80DTelemetry:
    """A set of direct, non-atomic LGA80D telemetry readings."""

    input_voltage: float
    output_voltage: float
    output_current: float
    temperature: float
    switching_frequency: float
    status_word: int


@dataclass(frozen=True)
class LGA80DStatus:
    """Raw PMBus status registers from one LGA80D output page."""

    word: int
    vout: int
    iout: int
    input: int
    temperature: int
    cml: int
    manufacturer: int

    # STATUS_WORD bits that indicate normal/intentional state, not a fault:
    # bit 6 (OFF) is asserted whenever the output isn't providing power
    # "regardless of the reason, including simply not being enabled", and
    # bit 11 (POWER_GOOD#) is a signal-state bit. Both must be excluded from
    # the fault summary or an intentionally-disabled, healthy unit reports
    # has_faults=True.
    _STATUS_WORD_NON_FAULT_BITS = (1 << 6) | (1 << 11)

    @property
    def has_faults(self) -> bool:
        return any((
            self.word & ~self._STATUS_WORD_NON_FAULT_BITS,
            self.vout,
            self.iout,
            self.input,
            self.temperature,
            self.cml,
            self.manufacturer,
        ))

@dataclass(frozen=True)
class LGA80DSnapshot:
    """Decoded 32-byte LGA80D SNAPSHOT (PMBus 0xEA), one atomic capture.

    Layout is ``snapshot_t`` in the MCU firmware (``commands/PowerCommands.c``),
    little-endian. Units follow the matching direct reads: ``switching_frequency``
    is whatever ``read_switching_frequency`` returns. ``output_current`` and
    ``max_output_current`` are signed (unloaded outputs have been seen reporting
    negative current) and are never clamped.
    """

    input_voltage: float
    output_voltage: float
    output_current: float
    max_output_current: float
    duty_cycle: float
    temperature: float
    switching_frequency: float
    vout_status: int
    iout_status: int
    input_status: int
    temperature_status: int
    cml_status: int
    manufacturer_status: int
    flash_status: int
    raw: bytes

    # Byte 22 of the snapshot is not defined in any datasheet checked: the
    # LGA80D TRN (Rev 2.6) and the ZL8802 (FN8760 Rev 3.00) and ZL8800 (FN7558
    # Rev 6.00) datasheets all list it only as "Flash Memory Status Byte, N/A,
    # Bit Field", with no bit or value definitions (a web search on
    # 2026-10-07 found nothing more). From the F2VCCINT failure analysis
    # (LGA80D_F2VCCINT_FA_notes.md): 0x00 on a supply that has stored a fault
    # record, 0xFF on one that is erased/empty. Both meanings are inferred
    # from observation, not from any datasheet.
    FLASH_STATUS_RECORD = 0x00
    FLASH_STATUS_EMPTY = 0xFF

    @property
    def is_stored_record(self) -> Optional[bool]:
        """Whether this snapshot is a stored fault record rather than live data.

        ``True`` if the flash status says a record is stored (0x00): per the
        failure-analysis notes the values are then the fault-time snapshot,
        not live readings, and the device stops updating the stored record
        until it is erased (``reset_snapshot``, output off). ``False`` if the
        flash is erased/empty (0xFF): the values are live readings. ``None``
        for any other flash status byte, which is not understood.

        Inferred from a byte that no datasheet defines (see the notes on the
        constants). Only 0xFF has been seen on hardware; the stored-record
        case (0x00) has not.
        """
        if self.flash_status == self.FLASH_STATUS_RECORD:
            return True
        if self.flash_status == self.FLASH_STATUS_EMPTY:
            return False
        return None

    def format_lines(self, indent: str = "    ") -> List[str]:
        """Human-readable multi-line summary, one string per line."""
        lines = []
        for label, value, unit in (
                ("VIN", self.input_voltage, "V"),
                ("VOUT", self.output_voltage, "V"),
                ("IOUT", self.output_current, "A"),
                ("IOUT max", self.max_output_current, "A"),
                ("duty cycle", self.duty_cycle, "%"),
                ("temperature", self.temperature, "C"),
                ("switching frequency", self.switching_frequency, "kHz")):
            lines.append("%s%-24s %.4g %s" % (indent, label, value, unit))
        for label, value in (
                ("STATUS_VOUT", self.vout_status),
                ("STATUS_IOUT", self.iout_status),
                ("STATUS_INPUT", self.input_status),
                ("STATUS_TEMPERATURE", self.temperature_status),
                ("STATUS_CML", self.cml_status),
                ("STATUS_MFR", self.manufacturer_status)):
            lines.append("%s%-24s 0x%02X" % (indent, label, value))
        stored = self.is_stored_record
        meaning = ("stored fault record, not live data" if stored
                   else "empty, live data" if stored is not None
                   else "unknown meaning")
        lines.append("%s%-24s 0x%02X (%s)" % (indent, "flash status",
                                              self.flash_status, meaning))
        lines.append("%sraw: %s" % (indent, " ".join("%02X" % b for b in self.raw)))
        return lines

    def __str__(self) -> str:
        return "\n".join(self.format_lines(indent=""))

    @classmethod
    def from_bytes(cls, raw: bytes) -> "LGA80DSnapshot":
        raw = bytes(raw)
        if len(raw) != SNAPSHOT_BYTES:
            raise ValueError(
                f"LGA80D snapshot must be {SNAPSHOT_BYTES} bytes, got {len(raw)}"
            )
        return cls(
            input_voltage=decode_linear11(raw[0:2]),
            output_voltage=decode_linear16u(raw[2:4]),
            output_current=decode_linear11(raw[4:6]),
            max_output_current=decode_linear11(raw[6:8]),
            duty_cycle=decode_linear11(raw[8:10]),
            temperature=decode_linear11(raw[10:12]),
            # bytes 12-13 are unused in the firmware's snapshot_t
            switching_frequency=decode_linear11(raw[14:16]),
            vout_status=raw[16],
            iout_status=raw[17],
            input_status=raw[18],
            temperature_status=raw[19],
            cml_status=raw[20],
            manufacturer_status=raw[21],
            flash_status=raw[22],
            raw=raw,
        )


class LGA80DReg(IntEnum):
    """Safe monitoring and basic control commands for the LGA80D."""
    PAGE         = 0x00
    OPERATION    = 0x01
    ON_OFF_CONFIG = 0x02
    STATUS_WORD  = 0x79
    STATUS_VOUT  = 0x7A
    STATUS_IOUT  = 0x7B
    STATUS_INPUT = 0x7C
    STATUS_TEMP  = 0x7D
    STATUS_CML   = 0x7E
    STATUS_MFR   = 0x80
    READ_VIN     = 0x88
    READ_VOUT    = 0x8B
    READ_IOUT    = 0x8C
    READ_TEMPERATURE_1 = 0x8D
    READ_TEMPERATURE_3 = 0x8F
    READ_FREQUENCY = 0x95
    SNAPSHOT_CONTROL = 0xF3

class LGA80D(Device):
    """Representation of an LGA80D device.

    Parameters
    ----------
    uart : UART
        Shared UART instance.
    address : int
        Device address on the bus.
    supply_name : str
        Textual identifier from the design (e.g. "F1VCCINT1").
    """
    def __init__(self, uart, address: int, supply_name: str):
        super().__init__(uart, address)
        self.supply_name = supply_name

    def _encode_command(self, reg: int, write: bool, payload: bytes = b"",
                        read_size: int = 1) -> bytes:
        return self._encode_ascii_command(
            "DC", self.address - 0x40, reg, write, payload, read_size
        )

    def _decode_response(self, raw: bytes) -> bytes:
        return self._decode_ascii_response(raw)

    @staticmethod
    def _paged_reg(command: int, page: int) -> int:
        if page not in (0, 1):
            raise ValueError("LGA80D page must be 0 or 1")
        return (page << 8) | int(command)

    def read_block(self, reg: int, length: int) -> bytes:
        raise NotImplementedError(
            "PMBus commands are not sequential registers; SMBus block reads "
            "require a device-specific transport"
        )

    def read_output_voltage(self, page: int = 0) -> float:
        raw = self.read_reg(self._paged_reg(LGA80DReg.READ_VOUT, page), size=2)
        return decode_linear16u(raw)

    def read_input_voltage(self, page: int = 0) -> float:
        raw = self.read_reg(self._paged_reg(LGA80DReg.READ_VIN, page), size=2)
        return decode_linear11(raw)

    def read_output_current(self, page: int = 0) -> float:
        raw = self.read_reg(self._paged_reg(LGA80DReg.READ_IOUT, page), size=2)
        return decode_linear11(raw)

    def read_temperature(self, page: int = 0, sensor: int = 1) -> float:
        if sensor == 1:
            command = LGA80DReg.READ_TEMPERATURE_1
        elif sensor == 3:
            command = LGA80DReg.READ_TEMPERATURE_3
        else:
            raise ValueError("LGA80D temperature sensor must be 1 or 3")
        raw = self.read_reg(self._paged_reg(command, page), size=2)
        return decode_linear11(raw)

    def read_switching_frequency(self, page: int = 0) -> float:
        raw = self.read_reg(self._paged_reg(LGA80DReg.READ_FREQUENCY, page), size=2)
        return decode_linear11(raw)

    def read_status_word(self, page: int = 0) -> int:
        raw = self.read_reg(self._paged_reg(LGA80DReg.STATUS_WORD, page), size=2)
        return int.from_bytes(raw, "little")

    def read_status(self, page: int = 0) -> LGA80DStatus:
        """Return the PMBus summary and category status registers."""
        return LGA80DStatus(
            word=self.read_status_word(page),
            vout=self.read_reg(self._paged_reg(LGA80DReg.STATUS_VOUT, page))[0],
            iout=self.read_reg(self._paged_reg(LGA80DReg.STATUS_IOUT, page))[0],
            input=self.read_reg(self._paged_reg(LGA80DReg.STATUS_INPUT, page))[0],
            temperature=self.read_reg(self._paged_reg(LGA80DReg.STATUS_TEMP, page))[0],
            cml=self.read_reg(self._paged_reg(LGA80DReg.STATUS_CML, page))[0],
            manufacturer=self.read_reg(self._paged_reg(LGA80DReg.STATUS_MFR, page))[0],
        )

    def read_telemetry(self, page: int = 0) -> LGA80DTelemetry:
        """Read current telemetry; unlike a future snapshot this is not atomic."""
        return LGA80DTelemetry(
            input_voltage=self.read_input_voltage(page),
            output_voltage=self.read_output_voltage(page),
            output_current=self.read_output_current(page),
            temperature=self.read_temperature(page),
            switching_frequency=self.read_switching_frequency(page),
            status_word=self.read_status_word(page),
        )

    @property
    def voltage(self) -> float:
        return self.read_output_voltage(page=0)

    def read_snapshot(self, page: int = 0) -> LGA80DSnapshot:
        """Capture and read the atomic 32-byte SNAPSHOT of one output page.

        Uses the MCU's ``SN`` ProgCom device: ``w SN <dev> <page> 00 01`` makes
        the MCU capture the register (PAGE, SNAPSHOT_CONTROL=0x01, wait, block
        read) and cache it, then eight 4-byte reads return the cache. The
        capture blocks the MCU's ProgCom task for ~70 ms uncontended and
        typically 200-400 ms while the monitor task polls (up to ~0.7 s seen
        on hardware), and for much longer if the I2C bus stays contended; the
        UART timeout is not changed here.

        Read-only: it does not erase the snapshot history. A new unit holds a
        factory-qualification fault until it is erased with
        :meth:`reset_snapshot` (output off). Requires firmware with the ``SN``
        device; older firmware answers ``e invalid device type``.

        Raises :class:`RegisterAccessError` if the capture or any read fails,
        including when another client captures a different supply or page
        between this capture and the reads (the MCU keeps a single capture).
        """
        if page not in (0, 1):
            raise ValueError("LGA80D page must be 0 or 1")
        dev = self.address - 0x40

        self.uart.write(self._encode_ascii_command(
            "SN", dev, page << 8, True, bytes([0x01])))
        try:
            self._check_ascii_write_response(self.uart.readline())
        except ValueError as exc:
            raise RegisterAccessError(
                f"LGA80D {self.supply_name} snapshot capture failed: {exc}"
            ) from exc

        raw = bytearray()
        while len(raw) < SNAPSHOT_BYTES:
            offset = len(raw)
            self.uart.write(self._encode_ascii_command(
                "SN", dev, (page << 8) | offset, False,
                read_size=_SNAPSHOT_READ_BYTES))
            try:
                chunk = self._decode_ascii_response(self.uart.readline())
            except ValueError as exc:
                raise RegisterAccessError(
                    f"LGA80D {self.supply_name} snapshot read at offset "
                    f"0x{offset:02X} failed: {exc}"
                ) from exc
            if len(chunk) != _SNAPSHOT_READ_BYTES:
                raise RegisterAccessError(
                    f"LGA80D {self.supply_name} snapshot read at offset "
                    f"0x{offset:02X} returned {len(chunk)} bytes; expected "
                    f"{_SNAPSHOT_READ_BYTES}"
                )
            raw.extend(chunk)
        return LGA80DSnapshot.from_bytes(bytes(raw))

    def reset_snapshot(self, page: int = 0) -> None:
        """Reset the snapshot history register for one output page.

        Modeled on the MCU firmware's ``snapdump_locked()``
        (LocalTasks.c): trigger a capture (``SNAPSHOT_CONTROL=0x01``),
        wait for the device to copy its live registers into the readable
        snapshot buffer, then reset the snapshot history
        (``SNAPSHOT_CONTROL=0x03``). The firmware's own comment on this
        exact sequence: "This will fail if the device is on" -- the
        output for this page must be off before calling this.

        This method has no visibility into board-level power sequencing
        and does not check that precondition itself. Prefer
        :meth:`Registry.reset_all_lga80d_snapshots`, which checks the MCU
        power state machine before calling this.
        """
        reg = self._paged_reg(LGA80DReg.SNAPSHOT_CONTROL, page)
        self.write_reg(reg, bytes([0x01]))
        time.sleep(SNAPSHOT_CAPTURE_DELAY_S)
        self.write_reg(reg, bytes([0x03]))

    def reset_all_snapshots(self) -> None:
        """Reset the snapshot history register for both output pages.

        Same caveat as :meth:`reset_snapshot`: this does not itself check
        that the outputs are off.
        """
        for page in (0, 1):
            self.reset_snapshot(page)
