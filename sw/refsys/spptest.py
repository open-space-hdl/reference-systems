# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Steps of TC-RS-HW-16: Space Packets that the Space Packet node of a reference system discards (OpenSPP,
specification of spp_core and register map). Every packet goes to virtual channel 1 as a CPTP packet; the node
discards it before the APID table (decapsulation and receive buffer) or after it (demultiplexer), counts it in its
counter of ERR_RX1 to ERR_RX6, writes its code, port and APID into LAST_ERROR and sets EVENTS.RX_DISCARD; it counts
no received packet (RX_PACKETS) and delivers nothing to a receive port:

- before the table: a Protocol Identifier other than 2 (0x11), a reserved byte other than 0 (0x12), a Space Packet
  of 6 bytes (0x14), a Packet Version Number 1 (0x15), a Packet Data Length that does not match the packet (0x16),
  a Space Packet of 8193 bytes, longer than the receive buffer (0x17), the idle APID 0x7FF (0x18);
- after the table: an APID whose entry is disabled (0x19) and Sequence Flags '01' on a port of the Octet String
  Service (0x1A).

The test APIDs lie outside the input APIDs of the set-up; the entry of NO_PATH_APID is disabled for the case and
restored to the path default afterwards (enabled, to port 0, no continuity check).
"""

from dataclasses import dataclass

from refsys.spp import SpacePacket, cptp_encode
from refsys.systest import SPP, SPP_RX_ERRORS, Registers, TestFailed, check, consecutive

TEST_APID = 0x020                       # the APID of the packets discarded before the table
NO_PATH_APID = 0x030                    # its entry disabled for the case
FLAGS_APID = 0x031                      # the path default: enabled, to port 0
ERR_RX = ("ERR_RX1", "ERR_RX2", "ERR_RX3", "ERR_RX4", "ERR_RX5", "ERR_RX6")
PATH_DEFAULT = SPP.field("RX_TABLE_ENTRY", "ENABLE", 1)


@dataclass(frozen=True)
class Case:
    """A packet that the node discards: what it is, the CPTP packet, the code, port and APID of LAST_ERROR."""

    name: str
    packet: bytes
    code: int
    port: int = 0
    apid: int = 0


def space_packet(apid: int, data: bytes, flags: int = 0b11, version: int = 0) -> bytes:
    """A Space Packet of the APID (Packet Version Number and Sequence Flags as given, count 0)."""
    sp = bytearray(SpacePacket(apid=apid, data=data, sequence_flags=flags).encode())
    sp[0] |= version << 5
    return bytes(sp)


def cases(tla: int, rx_buffer_bytes: int) -> list[Case]:
    """The packets of TC-RS-HW-16 for a node with the Target Logical Address tla and a receive buffer of
    rx_buffer_bytes bytes."""
    data = bytes(range(16))
    good = space_packet(TEST_APID, data)
    long_data = bytes((k * 7) & 0xFF for k in range(rx_buffer_bytes + 1 - 6))
    mismatch = bytearray(space_packet(TEST_APID, data))
    mismatch[5] += 2                                  # Packet Data Length of two bytes more than the packet holds
    return [
        Case("Protocol Identifier 3", bytes([tla, 0x03, 0x00, 0x00]) + good, 0x11),
        Case("reserved byte 0x5A", bytes([tla, 0x02, 0x5A, 0x00]) + good, 0x12),
        Case("Space Packet of 6 bytes", cptp_encode(good[:6], tla), 0x14, apid=TEST_APID),
        Case("Packet Version Number 1", cptp_encode(space_packet(TEST_APID, data, version=1), tla), 0x15,
             apid=TEST_APID),
        Case("Packet Data Length that does not match", cptp_encode(bytes(mismatch), tla), 0x16, apid=TEST_APID),
        Case(f"Space Packet of {rx_buffer_bytes + 1} bytes", cptp_encode(space_packet(TEST_APID, long_data), tla),
             0x17, apid=TEST_APID),
        Case("idle APID 0x7FF", cptp_encode(space_packet(0x7FF, data), tla), 0x18, apid=0x7FF),
        Case(f"APID 0x{NO_PATH_APID:03X} with its entry disabled", cptp_encode(space_packet(NO_PATH_APID, data), tla),
             0x19, apid=NO_PATH_APID),
        Case("Sequence Flags '01'", cptp_encode(space_packet(FLAGS_APID, data, flags=0b01), tla), 0x1A,
             apid=FLAGS_APID),
    ]


def write_entry(regs: Registers, node: int, apid: int, entry: int) -> None:
    """Writes an entry of the receive APID table and reads it back (OpenSPP user guide, programming sequence)."""
    busy = SPP.field("TABLE_STATUS", "RX_BUSY", 1)
    regs.wait(node + SPP.TABLE_STATUS, busy, 0)
    regs.write(node + SPP.RX_TABLE_APID, apid)
    regs.read(node + SPP.RX_TABLE_ENTRY)
    regs.write(node + SPP.RX_TABLE_ENTRY, entry)
    word = regs.read(node + SPP.RX_TABLE_ENTRY)
    check(word == entry, f"receive APID table entry of APID 0x{apid:03X}: 0x{word:08X}, written 0x{entry:08X}")


def discards(regs: Registers, ctx) -> dict[str, int]:
    """Sends every case and checks LAST_ERROR and ERR_RX1 to ERR_RX6 after each; returns the counts of ERR_RX1 to
    ERR_RX6 by register name (instance.register) for the check at the end of the test case."""
    s, setup = ctx.system, ctx.setup
    node = s.base(s.node)
    first = node + consecutive(SPP, ERR_RX)
    write_entry(regs, node, NO_PATH_APID, 0)
    counts = {name: 0 for name in ERR_RX}
    for case in cases(setup.system_tla, s.rx_buffer_bytes):
        regs.transport.transmit(setup.spp_channel, [case.packet])
        register, counter = SPP_RX_ERRORS[case.code]
        counts[register] += SPP.field(register, counter, 1)
        want = (SPP.field("LAST_ERROR", "CODE", case.code) | SPP.field("LAST_ERROR", "PORT", case.port)
                | SPP.field("LAST_ERROR", "APID", case.apid))
        # the packet travels on another virtual channel than the commands: wait until the node took it
        try:
            regs.wait(node + SPP.LAST_ERROR, 0xFFFFFFFF, want)
        except TestFailed as err:
            raise TestFailed(f"{case.name}: LAST_ERROR {err}") from None
        words = regs.read_block(first, len(ERR_RX))
        check(words == [counts[r] for r in ERR_RX],
              f"{case.name}: ERR_RX1 to ERR_RX6 {' '.join(f'{w:08X}' for w in words)}, expected "
              f"{' '.join(f'{counts[r]:08X}' for r in ERR_RX)}")
    write_entry(regs, node, NO_PATH_APID, PATH_DEFAULT)
    return {f"{s.node}.{name}": value for name, value in counts.items() if value}
