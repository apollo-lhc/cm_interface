class CMError(Exception):
    """Base class for all framework errors.

    All custom exceptions in this package inherit from ``CMError`` so that users
    can catch a single base class for any library‑specific problem.
    """
    
class UARTError(CMError):
    """Raised when the UART device cannot be opened or a read/write fails."""
    pass

class DeviceNotFound(CMError):
    """Raised when a requested device identifier does not exist in the registry."""
    pass

class RegisterAccessError(CMError):
    """Raised when a register read or write operation fails (e.g., timeout or
    malformed response)."""
    pass


class ClockNvmWriteRefused(CMError):
    """A write would touch an Si5395 NVM control register (burn / bank read)
    and the caller did not opt in with ``allow_nvm=True``."""
    pass


class FPGAInterfaceUnavailable(RegisterAccessError):
    """Raised when an FPGA bitfile does not acknowledge its generic endpoint."""
    pass


class McuProtocolError(CMError):
    """Raised when the MCU's register map does not match this client."""
    pass


class McuMagicError(McuProtocolError):
    """The System page did not start with the ``CMCU`` magic."""
    pass


class McuMapVersionError(McuProtocolError):
    """The firmware's register-map major version is not supported."""
    pass


class McuCoherencyError(McuProtocolError):
    """A generation-counted page changed during every read attempt."""
    pass


class McuCapabilityUnavailable(McuProtocolError):
    """The firmware does not advertise the capability this call needs."""
    pass
