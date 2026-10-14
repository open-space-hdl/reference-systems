"""Space Packets (CCSDS 133.0-B-2) and their transfer over SpaceWire and SpaceFibre (CPTP, ECSS-E-ST-50-53C)."""

from dataclasses import dataclass

CPTP_PROTOCOL_ID = 0x02  # ECSS-E-ST-50-53C 5.3
SEQ_UNSEGMENTED = 0b11   # Sequence Flags of an unsegmented packet (CCSDS 133.0-B-2 4.1.3.4.2)
PRIMARY_HEADER_BYTES = 6


class PacketError(Exception):
    """A packet that does not have the expected format."""


@dataclass(frozen=True)
class SpacePacket:
    """Fields of a Space Packet."""

    apid: int
    data: bytes
    sequence_count: int = 0
    telecommand: bool = False
    secondary_header: bool = False
    sequence_flags: int = SEQ_UNSEGMENTED

    def encode(self) -> bytes:
        """Packet Primary Header (version '000') and Packet Data Field (CCSDS 133.0-B-2 4.1)."""
        if not 0 <= self.apid < 1 << 11:
            raise ValueError("APID out of range (11 bits)")
        if not 1 <= len(self.data) <= 65536:
            raise ValueError("the Packet Data Field holds 1 to 65536 bytes")
        word0 = (self.telecommand << 12) | (self.secondary_header << 11) | self.apid
        word1 = (self.sequence_flags << 14) | (self.sequence_count & 0x3FFF)
        return (word0.to_bytes(2, "big") + word1.to_bytes(2, "big") + (len(self.data) - 1).to_bytes(2, "big")
                + bytes(self.data))

    @staticmethod
    def decode(packet: bytes) -> "SpacePacket":
        if len(packet) < PRIMARY_HEADER_BYTES + 1:
            raise PacketError(f"Space Packet of {len(packet)} bytes is shorter than 7 bytes")
        word0 = int.from_bytes(packet[0:2], "big")
        word1 = int.from_bytes(packet[2:4], "big")
        length = int.from_bytes(packet[4:6], "big") + 1
        if word0 >> 13 != 0:
            raise PacketError(f"Packet Version Number {word0 >> 13}, expected 0")
        if len(packet) != PRIMARY_HEADER_BYTES + length:
            raise PacketError(f"Packet Data Length gives {length} bytes, the packet has "
                              f"{len(packet) - PRIMARY_HEADER_BYTES}")
        return SpacePacket(apid=word0 & 0x7FF, data=bytes(packet[PRIMARY_HEADER_BYTES:]),
                           sequence_count=word1 & 0x3FFF, telecommand=bool(word0 & 0x1000),
                           secondary_header=bool(word0 & 0x0800), sequence_flags=word1 >> 14)


def cptp_encode(packet: bytes, target_logical_address: int, user_application: int = 0, path: bytes = b"") -> bytes:
    """CPTP packet (ECSS-E-ST-50-53C 5.4): Target SpaceWire Address, Target Logical Address, Protocol Identifier,
    Reserved, User Application, Space Packet."""
    return bytes(path) + bytes([target_logical_address, CPTP_PROTOCOL_ID, 0x00, user_application]) + bytes(packet)


def cptp_decode(packet: bytes, target_logical_address: int | None = None) -> tuple[int, bytes]:
    """Checks a CPTP packet whose Target SpaceWire Address has been removed; returns the User Application value and
    the Space Packet."""
    if len(packet) < 4:
        raise PacketError("CPTP packet shorter than its header")
    tla, pid, reserved, user_application = packet[:4]
    if pid != CPTP_PROTOCOL_ID or reserved != 0:
        raise PacketError(f"not a CPTP packet (protocol 0x{pid:02X}, reserved 0x{reserved:02X})")
    if target_logical_address is not None and tla != target_logical_address:
        raise PacketError(f"Target Logical Address 0x{tla:02X}, expected 0x{target_logical_address:02X}")
    return user_application, bytes(packet[4:])
