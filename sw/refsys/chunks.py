"""Authenticated chunks of OpenCrypto: ocr_framer cuts a packet into chunks, ocr_core encrypts every chunk with
AES-GCM and a generated IV and, with dense output, sends it as one byte string (OpenCrypto user guide, sections 4.5
and 4.6):

  bytes 0 to 11           IV: fixed field of the key slot (4 bytes) and invocation counter (8 bytes)
  bytes 12 to 15          AAD: the position of the chunk in its packet in bits 7:6 of byte 12, bytes 13 to 15 zero
  bytes 16 to 16 + n - 1  ciphertext of the n bytes of the chunk
  last 16 bytes           tag
"""

from dataclasses import dataclass

from Crypto.Cipher import AES

IV_BYTES = 12
AAD_BYTES = 4
TAG_BYTES = 16
OVERHEAD = IV_BYTES + AAD_BYTES + TAG_BYTES

# Position of a chunk in its packet (CCSDS sequence flags in bits 7:6 of the first AAD byte)
FIRST = 0b01
CONTINUATION = 0b00
LAST = 0b10
UNSEGMENTED = 0b11


class ChunkError(Exception):
    """A chunk that is malformed or whose tag does not authenticate it."""


@dataclass(frozen=True)
class Chunk:
    """A decrypted chunk."""

    fixed: int
    counter: int
    position: int
    plaintext: bytes


def position(first: bool, last: bool) -> int:
    return (last << 1) | first


def split(packet: bytes, chunk_bytes: int) -> list[tuple[int, bytes]]:
    """The chunks that the framer makes of one packet: (position, bytes) in order."""
    if not packet:
        raise ValueError("a packet has at least one byte")
    parts = [packet[k:k + chunk_bytes] for k in range(0, len(packet), chunk_bytes)]
    return [(position(k == 0, k == len(parts) - 1), part) for k, part in enumerate(parts)]


def aad(pos: int) -> bytes:
    return bytes([pos << 6, 0, 0, 0])


def iv(fixed: int, counter: int) -> bytes:
    """Deterministic IV of GCM 8.2.1: fixed field (32 bits) and invocation counter (64 bits)."""
    return fixed.to_bytes(4, "big") + counter.to_bytes(8, "big")


def encrypt(key: bytes, fixed: int, counter: int, pos: int, plaintext: bytes) -> bytes:
    """The byte string of one chunk as the core sends it (reference for tests)."""
    nonce = iv(fixed, counter)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=TAG_BYTES)
    cipher.update(aad(pos))
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return nonce + aad(pos) + ciphertext + tag


def decrypt(key: bytes, data: bytes) -> Chunk:
    """Authenticates and decrypts the byte string of one chunk."""
    if len(data) < OVERHEAD:
        raise ChunkError(f"chunk of {len(data)} bytes is shorter than IV, AAD and tag ({OVERHEAD} bytes)")
    nonce, header = data[:IV_BYTES], data[IV_BYTES:IV_BYTES + AAD_BYTES]
    ciphertext, tag = data[IV_BYTES + AAD_BYTES:-TAG_BYTES], data[-TAG_BYTES:]
    if header[0] & 0x3F or any(header[1:]):
        raise ChunkError(f"AAD {header.hex()} is not a chunk position")
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=TAG_BYTES)
    cipher.update(header)
    try:
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    except ValueError as err:
        raise ChunkError("tag does not authenticate the chunk") from err
    return Chunk(fixed=int.from_bytes(nonce[:4], "big"), counter=int.from_bytes(nonce[4:], "big"),
                 position=header[0] >> 6, plaintext=plaintext)


def reassemble(chunks: list[Chunk]) -> list[bytes]:
    """Packets from decrypted chunks in the order of their invocation counters; a gap or a broken sequence of
    positions raises ChunkError."""
    packets: list[bytes] = []
    current: list[bytes] | None = None
    ordered = sorted(chunks, key=lambda c: c.counter)
    for prev, chunk in zip([None] + ordered[:-1], ordered):
        if prev is not None and chunk.counter != prev.counter + 1:
            raise ChunkError(f"invocation counter jumps from {prev.counter} to {chunk.counter}: a chunk is missing")
        starts = chunk.position & FIRST
        if bool(starts) == (current is not None):
            raise ChunkError(f"chunk {chunk.counter}: position {chunk.position:02b} breaks the packet sequence")
        current = [] if starts else current
        current.append(chunk.plaintext)
        if chunk.position & LAST:
            packets.append(b"".join(current))
            current = None
    if current is not None:
        raise ChunkError("the last packet has no last chunk")
    return packets
