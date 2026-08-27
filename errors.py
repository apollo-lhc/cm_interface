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

