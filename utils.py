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
