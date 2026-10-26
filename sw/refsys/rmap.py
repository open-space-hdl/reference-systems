"""RMAP commands of an initiator and the replies of a target (ECSS-E-ST-50-52C).

A command is built as the complete packet that goes onto the link: the Target SpaceWire Address (path bytes, if
any), the header with its CRC and, for a write, the data with its CRC. A reply is checked field by field and its
data returned.
"""

from dataclasses import dataclass

PROTOCOL_ID = 0x01  # ECSS-E-ST-50-52C 5.1.3

# Status field of a reply (Table 5-4)
STATUS_TEXT = {
    0: "command executed successfully",
    1: "general error",
    2: "unused RMAP packet type or command code",
    3: "invalid key",
    4: "invalid data CRC",
    5: "early EOP",
    6: "too much data",
    7: "EEP",
    9: "verify buffer overrun",
    10: "RMAP command not implemented or not authorised",
    11: "RMW data length error",
    12: "invalid target logical address",
}


class RmapError(Exception):
    """A reply that is malformed or that does not belong to the command."""


def crc8(data: bytes, crc: int = 0) -> int:
    """CRC of ECSS-E-ST-50-52C 5.2: g(x) = x^8 + x^2 + x + 1 with the bits in reflected order, initial value 0.

    The least significant bit of a byte is the highest power of x, so the register shifts right and the feedback
    is 0xE0 (the same computation as crcUpdate of OpenRMAP).
    """
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xE0 if crc & 1 else crc >> 1
    return crc


@dataclass(frozen=True)
class Target:
    """How an initiator reaches an RMAP target and how the replies come back.

    path: Target SpaceWire Address (5.3.1.2), a path address: the leading data characters of the packet, one per
    routing switch on the way, each discarded by the routing switch that uses it (ECSS-E-ST-50-12C Rev.1 5.6.8.3).
    It is part of the packet, so a command starts with it.
    reply_path: path from the target back to the initiator; sent as the Reply Address field (5.1.6), padded with
    leading 0x00 bytes to 4, 8 or 12 bytes. The target removes the leading 0x00 bytes again and puts the path in
    front of the reply.
    """

    logical_address: int = 0xFE
    key: int = 0x00
    path: bytes = b""
    reply_path: bytes = b""
    initiator_logical_address: int = 0xFE


@dataclass(frozen=True)
class Reply:
    """Fields of a reply."""

    instruction: int
    status: int
    transaction_id: int
    data: bytes | None = None

    @property
    def ok(self) -> bool:
        return self.status == 0


def _reply_address(reply_path: bytes) -> tuple[bytes, int]:
    if len(reply_path) > 12:
        raise ValueError("the Reply Address field holds at most 12 bytes")
    if reply_path[:1] == b"\x00":
        raise ValueError("a reply path cannot start with 0x00 (leading 0x00 bytes are removed by the target)")
    words = (len(reply_path) + 3) // 4
    return bytes(4 * words - len(reply_path)) + reply_path, words


def _instruction(write: bool, verify: bool, reply: bool, increment: bool, ra_words: int) -> int:
    # 5.1.4: packet type 0b01 (command) in bits 7:6, command code in bits 5:2, Reply Address length in bits 1:0
    code = (write << 3) | (verify << 2) | (reply << 1) | increment
    return 0x40 | (code << 2) | ra_words


def _header(target: Target, instruction: int, reply_address: bytes, transaction_id: int, extended_address: int,
            address: int, length: int) -> bytes:
    if not 0 <= length < 1 << 24:
        raise ValueError("data length out of range (24 bits)")
    header = bytes([target.logical_address, PROTOCOL_ID, instruction, target.key]) + reply_address + bytes([
        target.initiator_logical_address,
        (transaction_id >> 8) & 0xFF, transaction_id & 0xFF,
        extended_address & 0xFF,
    ]) + (address & 0xFFFFFFFF).to_bytes(4, "big") + length.to_bytes(3, "big")
    return header + bytes([crc8(header)])


def write_command(target: Target, address: int, data: bytes, transaction_id: int, *, verify: bool = True,
                  reply: bool = True, increment: bool = True, extended_address: int = 0) -> bytes:
    """Write command (5.3.1) including the Target SpaceWire Address."""
    reply_address, words = _reply_address(target.reply_path)
    instruction = _instruction(True, verify, reply, increment, words)
    header = _header(target, instruction, reply_address, transaction_id, extended_address, address, len(data))
    return target.path + header + bytes(data) + bytes([crc8(data)])


def read_command(target: Target, address: int, length: int, transaction_id: int, *, increment: bool = True,
                 extended_address: int = 0) -> bytes:
    """Read command (5.4.1) including the Target SpaceWire Address."""
    reply_address, words = _reply_address(target.reply_path)
    instruction = _instruction(False, False, True, increment, words)
    return target.path + _header(target, instruction, reply_address, transaction_id, extended_address, address,
                                 length)


def command(target: Target, code: int, address: int, length: int, transaction_id: int, *, data: bytes | None = None,
            extended_address: int = 0, ra_words: int | None = None, packet_type: int = 0b01) -> bytes:
    """Any command, also one that a target refuses, for the tests of a target: the command code (bits 5:2 of the
    instruction: write, verify, reply, increment), the packet type (bits 7:6), the Reply Address field of ra_words
    words (by default the fewest that hold the reply path) and, for data, the data and its CRC."""
    reply_address, words = _reply_address(target.reply_path)
    if ra_words is not None:
        if ra_words < words:
            raise ValueError("the Reply Address field is too short for the reply path")
        reply_address, words = bytes(4 * (ra_words - words)) + reply_address, ra_words
    instruction = (packet_type << 6) | (code << 2) | words
    header = _header(target, instruction, reply_address, transaction_id, extended_address, address, length)
    packet = target.path + header
    return packet + bytes(data) + bytes([crc8(data)]) if data is not None else packet


def rmw_command(target: Target, address: int, data: bytes, mask: bytes, transaction_id: int, *,
                extended_address: int = 0) -> bytes:
    """Read-modify-write command (5.5.1): the data and the mask, each Data Length / 2 bytes."""
    if len(data) != len(mask):
        raise ValueError("the data and the mask of a read-modify-write command have the same length")
    return command(target, 0b0111, address, 2 * len(data), transaction_id, data=bytes(data) + bytes(mask),
                   extended_address=extended_address)


@dataclass(frozen=True)
class AnyReply:
    """The fields of a reply of either format, whatever the command (for the tests of a target)."""

    instruction: int
    status: int
    target_logical_address: int
    transaction_id: int
    data: bytes | None


def parse_any_reply(packet: bytes) -> AnyReply:
    """Checks the header CRC and the format of a reply whose Reply SpaceWire Address has been removed: the write reply
    of 8 bytes, or the read reply of 12 bytes with the data of its Data Length and their CRC."""
    if len(packet) == 8:
        if crc8(packet) != 0:
            raise RmapError("header CRC error in the reply")
        data = None
    else:
        if len(packet) < 13 or crc8(packet[:12]) != 0:
            raise RmapError(f"reply of {len(packet)} bytes: neither a write reply nor a read reply with a good CRC")
        length = int.from_bytes(packet[8:11], "big")
        data = packet[12:]
        if len(data) != length + 1 or crc8(data) != 0:
            raise RmapError(f"read reply with {len(data)} bytes after the header for a Data Length of {length}")
        data = bytes(data[:-1])
    if packet[1] != PROTOCOL_ID or packet[2] >> 6 != 0:
        raise RmapError(f"not an RMAP reply (protocol 0x{packet[1]:02X}, instruction 0x{packet[2]:02X})")
    return AnyReply(packet[2], packet[3], packet[4], (packet[5] << 8) | packet[6], data)


def parse_reply(packet: bytes, target: Target, transaction_id: int, *, read: bool) -> Reply:
    """Checks a write reply (5.3.2) or read reply (5.4.2) whose Reply SpaceWire Address has been removed."""
    size = 12 if read else 8
    if len(packet) < size:
        raise RmapError(f"reply of {len(packet)} bytes is shorter than its header ({size} bytes)")
    header = packet[:size]
    if crc8(header) != 0:
        raise RmapError("header CRC error in the reply")
    ila, pid, instruction, status, tla = header[:5]
    tid = (header[5] << 8) | header[6]
    if ila != target.initiator_logical_address or tla != target.logical_address:
        raise RmapError(f"reply addressed from 0x{tla:02X} to 0x{ila:02X}")
    if pid != PROTOCOL_ID or instruction >> 6 != 0:
        raise RmapError(f"not an RMAP reply (protocol 0x{pid:02X}, instruction 0x{instruction:02X})")
    if bool(instruction & 0x20) == read:
        raise RmapError(f"reply to a {'write' if read else 'read'} command")
    if tid != transaction_id:
        raise RmapError(f"transaction identifier {tid}, expected {transaction_id}")
    if not read:
        if len(packet) != size:
            raise RmapError("write reply longer than its header")
        return Reply(instruction, status, tid)
    length = int.from_bytes(header[8:11], "big")
    data = packet[size:]
    if status == 0 or len(data) == length + 1:
        if len(data) != length + 1:
            raise RmapError(f"read reply with {len(data)} bytes after the header, expected {length} + CRC")
        if crc8(data) != 0:
            raise RmapError("data CRC error in the read reply")
        return Reply(instruction, status, tid, bytes(data[:-1]))
    return Reply(instruction, status, tid)


def status_text(status: int) -> str:
    return STATUS_TEXT.get(status, "reserved status code")
