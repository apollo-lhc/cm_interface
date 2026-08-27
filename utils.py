def crc8(data: bytes) -> bytes:
    """Simple XOR‑based checksum (placeholder).

    The real hardware may use a different CRC algorithm; replace this
    implementation with the appropriate one when the specification is known.
    """
    chk = 0
    for b in data:
        chk ^= b
    return bytes([chk])

def pack_uint16(v: int) -> bytes:
    """Pack an unsigned 16‑bit integer into big‑endian bytes."""
    return v.to_bytes(2, "big")

def unpack_uint16(b: bytes) -> int:
    """Unpack big‑endian bytes into an unsigned 16‑bit integer."""
    return int.from_bytes(b, "big")


def decode_linear11(data: bytes) -> float:
    """Decode a little-endian PMBus Linear11 value."""
    if len(data) != 2:
        raise ValueError("Linear11 values must contain exactly two bytes")
    raw = int.from_bytes(data, "little")
    exponent = (raw >> 11) & 0x1F
    mantissa = raw & 0x7FF
    if exponent & 0x10:
        exponent -= 0x20
    if mantissa & 0x400:
        mantissa -= 0x800
    return float(mantissa * (2 ** exponent))


def decode_linear16u(data: bytes, exponent: int = -13) -> float:
    """Decode an unsigned little-endian PMBus Linear16 value."""
    if len(data) != 2:
        raise ValueError("Linear16 values must contain exactly two bytes")
    return float(int.from_bytes(data, "little") * (2 ** exponent))


def encode_linear16u(value: float, exponent: int = -13) -> bytes:
    """Encode an unsigned PMBus Linear16 value as little-endian bytes."""
    raw = round(value / (2 ** exponent))
    if not 0 <= raw <= 0xFFFF:
        raise ValueError("value is outside the Linear16 representable range")
    return raw.to_bytes(2, "little")
