"""Unit tests of refsys.spp, refsys.chunks and refsys.regs."""

from pathlib import Path

import pytest
from Crypto.Cipher import AES

from refsys import chunks
from refsys.regs import RegisterMap
from refsys.spp import PacketError, SpacePacket, cptp_decode, cptp_encode

CORES = Path(__file__).resolve().parents[2] / "cores"


def test_space_packet_header():
    packet = SpacePacket(apid=0x123, data=b"\x01\x02\x03", sequence_count=5).encode()
    assert packet == bytes([0x01, 0x23, 0xC0, 0x05, 0x00, 0x02, 0x01, 0x02, 0x03])
    tc = SpacePacket(apid=0x7FF, data=b"\x00", telecommand=True, secondary_header=True, sequence_count=0x3FFF)
    assert tc.encode()[:4] == bytes([0x1F, 0xFF, 0xFF, 0xFF])
    assert SpacePacket.decode(tc.encode()) == tc


@pytest.mark.parametrize("packet", [b"\x00\x01\xc0\x00\x00", b"\x20\x01\xc0\x00\x00\x00\x00",
                                    b"\x00\x01\xc0\x00\x00\x01\x00"])
def test_space_packet_rejects(packet):
    with pytest.raises(PacketError):
        SpacePacket.decode(packet)


def test_cptp():
    packet = cptp_encode(b"\xaa\xbb", 0xFE, user_application=0x12, path=b"\x05")
    assert packet == b"\x05\xfe\x02\x00\x12\xaa\xbb"
    assert cptp_decode(packet[1:], 0xFE) == (0x12, b"\xaa\xbb")
    with pytest.raises(PacketError):
        cptp_decode(b"\xfe\x01\x00\x00\xaa")
    with pytest.raises(PacketError):
        cptp_decode(packet[1:], 0x40)


def test_split_positions():
    assert [p for p, _ in chunks.split(b"x", 4)] == [chunks.UNSEGMENTED]
    parts = chunks.split(bytes(range(10)), 4)
    assert [p for p, _ in parts] == [chunks.FIRST, chunks.CONTINUATION, chunks.LAST]
    assert b"".join(c for _, c in parts) == bytes(range(10))


def test_chunk_round_trip_and_format():
    key = bytes(range(32))
    data = chunks.encrypt(key, 0x11223344, 0x0102030405060708, chunks.LAST, b"payload")
    assert data[:12] == bytes.fromhex("11223344 0102030405060708")
    assert data[12:16] == bytes([0x80, 0, 0, 0])
    assert len(data) == chunks.OVERHEAD + len(b"payload")
    chunk = chunks.decrypt(key, data)
    assert (chunk.fixed, chunk.counter, chunk.position, chunk.plaintext) == (
        0x11223344, 0x0102030405060708, chunks.LAST, b"payload")


def test_chunk_is_plain_gcm():
    # the chunk is the GCM encryption of the chunk bytes with the IV and the 4-byte AAD (GCM 7.1)
    key, nonce, header = bytes(16), bytes(12), bytes([0x40, 0, 0, 0])
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(header)
    ciphertext, tag = cipher.encrypt_and_digest(b"abc")
    assert chunks.encrypt(key, 0, 0, chunks.FIRST, b"abc") == nonce + header + ciphertext + tag


@pytest.mark.parametrize("index", [0, 12, 13, 16, -1])
def test_chunk_tamper_detected(index):
    key = bytes(16)
    data = bytearray(chunks.encrypt(key, 1, 2, chunks.UNSEGMENTED, b"secret"))
    data[index] ^= 0x01
    with pytest.raises(chunks.ChunkError):
        chunks.decrypt(key, bytes(data))


def test_reassemble():
    def chunk(counter, pos, text):
        return chunks.Chunk(fixed=0, counter=counter, position=pos, plaintext=text)
    ordered = [chunk(5, chunks.FIRST, b"ab"), chunk(6, chunks.LAST, b"c"), chunk(7, chunks.UNSEGMENTED, b"d")]
    assert chunks.reassemble(list(reversed(ordered))) == [b"abc", b"d"]
    with pytest.raises(chunks.ChunkError):
        chunks.reassemble([ordered[0], ordered[2]])          # counter gap
    with pytest.raises(chunks.ChunkError):
        chunks.reassemble([chunk(1, chunks.LAST, b"x")])     # last without first
    with pytest.raises(chunks.ChunkError):
        chunks.reassemble([chunk(1, chunks.FIRST, b"x")])    # first without last


def test_register_map_from_header():
    ocr = RegisterMap(CORES / "opencrypto" / "sw" / "ocr_regs.h", "OCR")
    assert ocr.REGMAP_ID == 0x4F435201
    assert ocr.KEY_LOAD == 0x06C
    assert ocr.KEY(3) == 0x04C
    assert ocr.field("KEY_LOAD", "START", 1) == 1 << 31
    assert ocr.get("KEY_LOAD", "SLOT", 0x80000105) == 5
    with pytest.raises(AttributeError):
        _ = ocr.NO_SUCH_REGISTER
    with pytest.raises(ValueError):
        ocr.field("KEY_LOAD", "SLOT", 0x100)
    ofb = RegisterMap(CORES / "openfibre" / "sw" / "ofb_regs.h", "OFB")
    assert ofb.LANE_STATUS(2) == 0x144
