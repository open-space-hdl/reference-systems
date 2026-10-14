"""Unit tests of refsys.rmap."""

import pytest

from refsys import rmap


def reverse8(byte: int) -> int:
    return int(f"{byte:08b}"[::-1], 2)


def crc_msb_first(data: bytes) -> int:
    """g(x) = x^8 + x^2 + x + 1 computed most significant bit first (polynomial 0x07)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


@pytest.mark.parametrize("data", [b"", b"\x00", b"\x01", b"\xfe\x01\x4c\x00", bytes(range(256))])
def test_crc_is_the_reflected_polynomial(data):
    # ECSS-E-ST-50-52C 5.2: the least significant bit of a byte is the highest power of x
    assert rmap.crc8(data) == reverse8(crc_msb_first(bytes(reverse8(b) for b in data)))


def test_crc_of_field_and_crc_is_zero():
    data = bytes(range(37))
    assert rmap.crc8(data + bytes([rmap.crc8(data)])) == 0


TARGET = rmap.Target(logical_address=0xFE, key=0x20, path=b"\x03", reply_path=b"\x01", initiator_logical_address=0x67)


def test_read_command_fields():
    packet = rmap.read_command(TARGET, 0x00012340, 8, 0x1234)
    assert packet[0] == 0x03                          # Target SpaceWire Address
    header = packet[1:]
    assert header[:4] == bytes([0xFE, 0x01, 0x4D, 0x20])  # read, reply, incrementing, 4-byte reply address
    assert header[4:8] == b"\x00\x00\x00\x01"         # Reply Address padded with leading zeros
    assert header[8:12] == bytes([0x67, 0x12, 0x34, 0x00])
    assert header[12:16] == bytes([0x00, 0x01, 0x23, 0x40])
    assert header[16:19] == bytes([0, 0, 8])
    assert len(header) == 20 and rmap.crc8(header) == 0


def test_write_command_fields():
    packet = rmap.write_command(TARGET, 0x10, b"\x11\x22\x33\x44", 7, verify=True, reply=True, increment=True)
    header, data = packet[1:21], packet[21:]
    assert header[2] == 0x7D                          # write, verify, reply, incrementing, 4-byte reply address
    assert rmap.crc8(header) == 0
    assert data[:4] == b"\x11\x22\x33\x44" and rmap.crc8(data) == 0
    no_reply = rmap.write_command(rmap.Target(), 0, b"\x00", 0, verify=False, reply=False, increment=False)
    assert no_reply[2] == 0x60


def test_reply_address_without_leading_zero():
    with pytest.raises(ValueError):
        rmap.read_command(rmap.Target(reply_path=b"\x00\x01"), 0, 4, 0)


def reply(read: bool, status: int, tid: int, data: bytes = b"", ila: int = 0x67, tla: int = 0xFE) -> bytes:
    instruction = 0x0D if read else 0x3D
    header = bytes([ila, 0x01, instruction, status, tla, tid >> 8, tid & 0xFF])
    if read:
        header += bytes([0]) + len(data).to_bytes(3, "big")
    packet = header + bytes([rmap.crc8(header)])
    return packet + data + bytes([rmap.crc8(data)]) if read else packet


def test_parse_read_reply():
    r = rmap.parse_reply(reply(True, 0, 0x1234, b"\x01\x02\x03\x04"), TARGET, 0x1234, read=True)
    assert r.ok and r.data == b"\x01\x02\x03\x04"


def test_parse_write_reply_with_error_status():
    r = rmap.parse_reply(reply(False, 3, 9), TARGET, 9, read=False)
    assert not r.ok and r.status == 3 and rmap.status_text(r.status) == "invalid key"


@pytest.mark.parametrize("packet, tid, read", [
    (reply(True, 0, 5, b"\xaa") [:-1] + b"\x00", 5, True),   # data CRC
    (reply(False, 0, 5), 6, False),                           # transaction identifier
    (reply(False, 0, 5), 5, True),                            # reply to a write, read expected
    (reply(False, 0, 5, tla=0x40), 5, False),                 # other target
    (b"\x67\x01\x3d\x00\xfe\x00\x05\x00", 5, False),          # header CRC
])
def test_parse_reply_rejects(packet, tid, read):
    with pytest.raises(rmap.RmapError):
        rmap.parse_reply(packet, TARGET, tid, read=read)
