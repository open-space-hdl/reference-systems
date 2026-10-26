# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""TC-RS-HW-15: the RMAP target of a reference system against commands that it refuses or treats specially, built
byte by byte (refsys.rmap.command), with the reply, TGT_LAST and the counters of every case (OpenRMAP, specification
of omap_target and register map):

- statuses with a reply: invalid target logical address (12), invalid key on a write that leaves the register
  unchanged (3), both at once (12, TG-AU-08), an invalid command code (2, the write reply), a data CRC error of a
  verified write (4, nothing written) and of a non-verified one (4, the data written, TG-EX-02), an early EOP (5)
  and too much data (6) of a verified write (nothing written), a verified write longer than the verify buffer (9),
  a read-modify-write of 3 bytes (11), an Extended Address 1 (10) and a write outside the register windows of the
  system (1, the decoder answers DECERR);
- packets that the target discards without a reply and without a change of TGT_LAST: a header CRC error, a header
  that ends early, a reply packet, a command of a reserved packet type (counted with status 2) and, before the
  target, a CPTP packet on the virtual channel of RMAP (PKT_CNT.DISCARDED);
- commands that execute: a non-verified write, a write without a reply, a single-address write and read of two
  words, a write and a read of no data, a read-modify-write of edac0 IRQ_ENA and reads with a Reply Address field of
  8 and 12 bytes;
- the windows: window 3 opened for the registers of the RMAP target; window 0 restricted to verified writes (a
  non-verified write is refused with 10), then to no writes (a verified write outside window 3 is refused with 10),
  and restored with a write through window 3; window 3 closed again.

The scratch registers are WIN_BASE and WIN_LAST of window 3, disabled after the reset (TC-RS-HW-04 uses them too),
and IRQ_ENA of the ECC monitor; all are restored. At the end the counters of the target equal the sum of the cases,
every other error register is still 0 (systest.caused), and the error registers are cleared. A run that stops inside
the test case may leave window 0 restricted: program the device again.
"""

import random
from dataclasses import dataclass

from refsys import rmap, systest
from refsys.systest import EDAC_IRQ_ENA, OMAP, Registers, check

# command codes: write, verify, reply, increment (bits 5:2 of the instruction)
WRITE, VERIFY, REPLY, INCREMENT = 8, 4, 2, 1
VERIFIED_WRITE = WRITE | VERIFY | REPLY | INCREMENT
READ = REPLY | INCREMENT
INVALID_WITH_REPLY = 0b0110                     # the only invalid command code with the reply bit
RESERVED_TYPE = 0b10                            # packet type of no command and no reply
# WIN_CTRL of the windows (OpenRMAP register map)
WIN = {f: OMAP.field("WIN_CTRL", f, 1) for f in ("ENABLE", "READ", "WRITE", "VERIFIED_ONLY", "RMW", "SINGLE")}
LAST_WORDS = ("TGT_LAST", "TGT_LAST_TID", "TGT_LAST_ADDR", "TGT_LAST_ADDR_EXT")


@dataclass
class Expected:
    """What a case leaves in TGT_LAST: status, instruction, whether a reply was sent, TID, address, Extended
    Address."""

    status: int
    instruction: int
    replied: bool
    tid: int
    address: int
    extended: int = 0


class RmapErrors:
    """The cases of TC-RS-HW-15 on one system; counts what the target must count."""

    def __init__(self, regs: Registers, ctx: systest.Context, unmapped: int):
        self.regs, self.ctx = regs, ctx
        s = ctx.system
        self.base = s.base(s.rmap)
        self.target = regs.target
        self.channel = ctx.setup.rmap_channel
        self.scratch = self.base + OMAP.WIN_BASE(s.rmap_windows - 1)
        self.scratch2 = self.base + OMAP.WIN_LAST(s.rmap_windows - 1)
        self.irq_ena = s.base(s.ecc) + EDAC_IRQ_ENA
        self.unmapped = unmapped
        self.rng = random.Random(f"{ctx.seed} TC-RS-HW-15")
        self.counts: dict[tuple[str, str], int] = {}
        self.last: Expected | None = None          # TGT_LAST after the last command with an indication

    # --- sending -------------------------------------------------------------------------------------------------
    def tid(self) -> int:
        return self.regs.next_tid()

    def request(self, packet: bytes) -> rmap.AnyReply:
        return rmap.parse_any_reply(self.regs.transport.request(self.channel, packet))

    def silent(self, packet: bytes) -> None:
        self.regs.transport.transmit(self.channel, [packet])

    def count(self, register: str, counter: str, n: int = 1) -> None:
        self.counts[(register, counter)] = self.counts.get((register, counter), 0) + n

    @staticmethod
    def instruction(packet: bytes, path: bytes) -> int:
        return packet[len(path) + 2]

    # --- checks --------------------------------------------------------------------------------------------------
    def check_last(self, what: str) -> None:
        """TGT_LAST, TGT_LAST_TID, TGT_LAST_ADDR and TGT_LAST_ADDR_EXT show self.last; the read itself becomes the
        last command with an indication."""
        address = self.base + systest.consecutive(OMAP, LAST_WORDS)
        tid = self.regs.tid + 1                       # the read below takes the next transaction identifier
        words = self.regs.read_block(address, len(LAST_WORDS))
        e = self.last
        if e is not None:
            want = [e.status | e.instruction << 8 | self.target.initiator_logical_address << 16 | e.replied << 24,
                    e.tid, e.address, e.extended]
            check(words == want, f"{what}: TGT_LAST, _TID, _ADDR, _ADDR_EXT {systest.hex_words(words)}, expected "
                  f"{systest.hex_words(want)}")
        read = rmap.read_command(self.target, address, 4 * len(LAST_WORDS), tid)
        self.last = Expected(0, self.instruction(read, self.target.path), True, tid, address)
        self.count("TGT_CNT_OK", "READ")

    def replied(self, what: str, packet: bytes, status: int, tid: int, address: int, *, extended: int = 0,
                tla: int | None = None, data: bytes | None = None) -> rmap.AnyReply:
        """A command that gets a reply with the status; TGT_LAST shows it."""
        reply = self.request(packet)
        want_tla = self.target.logical_address if tla is None else tla
        check((reply.status, reply.transaction_id, reply.target_logical_address) == (status, tid, want_tla),
              f"{what}: reply with status {reply.status} ({rmap.status_text(reply.status)}), TID {reply.transaction_id}"
              f", TLA 0x{reply.target_logical_address:02X}; expected status {status}, TID {tid}, TLA 0x{want_tla:02X}")
        if data is not None:
            check(reply.data == data, f"{what}: reply data {(reply.data or b'').hex()}, expected {data.hex()}")
        self.last = Expected(status, self.instruction(packet, self.target.path), True, tid, address, extended)
        self.check_last(what)
        return reply

    def value(self, address: int, want: int, what: str) -> None:
        got = self.regs.read(address)
        self.count("TGT_CNT_OK", "READ")
        check(got == want, f"{what}: 0x{address:08X} holds 0x{got:08X}, expected 0x{want:08X}")
        self.last = None                              # TGT_LAST shows that read: checked again from the next case

    def write(self, address: int, value: int) -> None:
        self.regs.write(address, value)
        self.count("TGT_CNT_OK", "WRITE")
        self.last = None

    # --- the cases -----------------------------------------------------------------------------------------------
    def statuses(self) -> None:
        t, scratch = self.target, self.scratch
        old, new = self.rng.getrandbits(32), self.rng.getrandbits(32)
        self.write(scratch, old)
        word = new.to_bytes(4, "little")
        other_tla = rmap.Target(0x55, t.key, t.path, t.reply_path, t.initiator_logical_address)
        wrong_key = rmap.Target(t.logical_address, t.key ^ 0xA5, t.path, t.reply_path, t.initiator_logical_address)
        both = rmap.Target(0x55, t.key ^ 0xA5, t.path, t.reply_path, t.initiator_logical_address)
        tid = self.tid()
        self.replied("invalid target logical address", rmap.read_command(other_tla, scratch, 4, tid), 12, tid, scratch,
                     tla=0x55, data=b"")
        self.count("TGT_CNT_ERR6", "TLA")
        tid = self.tid()
        self.replied("invalid key on a write", rmap.write_command(wrong_key, scratch, word, tid), 3, tid, scratch)
        self.count("TGT_CNT_ERR2", "KEY")
        self.value(scratch, old, "the write with the invalid key")
        self.check_last("read back")
        tid = self.tid()
        self.replied("invalid target logical address and key", rmap.write_command(both, scratch, word, tid), 12, tid,
                     scratch, tla=0x55)
        self.count("TGT_CNT_ERR6", "TLA")
        tid = self.tid()
        packet = rmap.command(t, INVALID_WITH_REPLY, scratch, 4, tid, data=word)
        self.replied("invalid command code 0110", packet, 2, tid, scratch)
        self.count("TGT_CNT_ERR1", "UNUSED")
        tid = self.tid()
        packet = rmap.write_command(t, scratch, word, tid)
        self.replied("data CRC error of a verified write", packet[:-1] + bytes([packet[-1] ^ 0x5A]), 4, tid, scratch)
        self.count("TGT_CNT_ERR2", "DATA_CRC")
        self.value(scratch, old, "the verified write with the data CRC error")
        self.check_last("read back")
        tid = self.tid()
        two = self.rng.getrandbits(64).to_bytes(8, "little")
        packet = rmap.write_command(t, scratch, two, tid)
        # Data Length 8, but 4 data bytes and a CRC before the EOP
        early = packet[:len(packet) - 9] + two[:4] + bytes([rmap.crc8(two[:4])])
        self.replied("early EOP of a verified write", early, 5, tid, scratch)
        self.count("TGT_CNT_ERR3", "EARLY_EOP")
        self.value(scratch, old, "the verified write with the early EOP")
        self.check_last("read back")
        tid = self.tid()
        packet = rmap.write_command(t, scratch, word, tid)
        extra = packet[:-5] + word + word + bytes([rmap.crc8(word + word)])
        self.replied("too much data for a verified write", extra, 6, tid, scratch)
        self.count("TGT_CNT_ERR3", "TOO_MUCH")
        self.value(scratch, old, "the verified write with too much data")
        self.check_last("read back")
        tid = self.tid()
        buffer = self.ctx.system.file.generic(self.ctx.system.rmap, "BufferBytes_g")
        long_data = bytes(self.rng.getrandbits(8) for _ in range(buffer + 4))
        self.replied(f"verified write of {buffer + 4} bytes", rmap.write_command(t, scratch, long_data, tid), 9, tid,
                     scratch)
        self.count("TGT_CNT_ERR4", "VERIFY_OVERRUN")
        self.value(scratch, old, f"the verified write of {buffer + 4} bytes")
        self.check_last("read back")
        tid = self.tid()
        packet = rmap.command(t, 0b0111, self.irq_ena, 3, tid, data=b"\x01\x00\x00")
        self.replied("read-modify-write of 3 bytes", packet, 11, tid, self.irq_ena, data=b"")
        self.count("TGT_CNT_ERR5", "RMW_LENGTH")
        tid = self.tid()
        self.replied("Extended Address 1", rmap.read_command(t, scratch, 4, tid, extended_address=1), 10, tid, scratch,
                     extended=1, data=b"")
        self.count("TGT_CNT_ERR5", "NOT_AUTH")
        tid = self.tid()
        self.replied(f"write at 0x{self.unmapped:08X}, outside the register windows",
                     rmap.write_command(t, self.unmapped, word, tid), 1, tid, self.unmapped)
        self.count("TGT_CNT_ERR1", "GENERAL")
        # a non-verified write with a data CRC error writes its data (TG-EX-02)
        tid = self.tid()
        packet = rmap.write_command(t, scratch, word, tid, verify=False)
        self.replied("data CRC error of a non-verified write", packet[:-1] + bytes([packet[-1] ^ 0x5A]), 4, tid,
                     scratch)
        self.count("TGT_CNT_ERR2", "DATA_CRC")
        self.value(scratch, new, "the non-verified write with the data CRC error")
        self.check_last("read back")

    def discards(self) -> None:
        """Packets without a reply: the next reply belongs to the next command (its TID is checked), TGT_LAST is
        unchanged."""
        t, scratch = self.target, self.scratch
        packet = rmap.read_command(t, scratch, 4, self.tid())
        self.silent(packet[:-1] + bytes([packet[-1] ^ 0x01]))
        self.count("TGT_CNT_HDR", "HDR_CRC")
        self.check_last("header CRC error")
        self.silent(rmap.read_command(t, scratch, 4, self.tid())[:len(t.path) + 9])
        self.count("TGT_CNT_HDR", "HDR_SHORT")
        self.check_last("header that ends early")
        self.silent(rmap.command(t, READ, scratch, 4, self.tid(), packet_type=0b00))
        self.count("TGT_CNT_ERR6", "REPLY_RX")
        self.check_last("reply packet")
        tid = self.tid()
        packet = rmap.command(t, READ, scratch, 4, tid, packet_type=RESERVED_TYPE)
        self.silent(packet)
        self.count("TGT_CNT_ERR1", "UNUSED")
        self.last = Expected(2, self.instruction(packet, t.path), False, tid, scratch)
        self.check_last("command of a reserved packet type")
        # a CPTP packet: Protocol Identifier 2, not RMAP
        self.silent(t.path + bytes([t.logical_address, 0x02, 0x00, 0x00]) + bytes(8))
        self.count("PKT_CNT", "DISCARDED")
        self.check_last("CPTP packet")

    def executions(self) -> None:
        t, scratch = self.target, self.scratch
        values = [self.rng.getrandbits(32) for _ in range(4)]
        tid = self.tid()
        packet = rmap.write_command(t, scratch, values[0].to_bytes(4, "little"), tid, verify=False)
        self.replied("non-verified write", packet, 0, tid, scratch)
        self.count("TGT_CNT_OK", "WRITE")
        self.value(scratch, values[0], "the non-verified write")
        self.check_last("read back")
        tid = self.tid()
        packet = rmap.write_command(t, scratch, values[1].to_bytes(4, "little"), tid, reply=False)
        self.silent(packet)
        self.count("TGT_CNT_OK", "WRITE")
        self.last = Expected(0, self.instruction(packet, t.path), False, tid, scratch)
        self.check_last("write without a reply")
        self.value(scratch, values[1], "the write without a reply")
        self.check_last("read back")
        tid = self.tid()
        data = values[2].to_bytes(4, "little") + values[3].to_bytes(4, "little")
        self.replied("single-address write of two words", rmap.write_command(t, scratch, data, tid, increment=False), 0,
                     tid, scratch)
        self.count("TGT_CNT_OK", "WRITE")
        self.value(scratch, values[3], "the single-address write of two words")
        self.check_last("read back")
        tid = self.tid()
        self.replied("single-address read of two words", rmap.read_command(t, scratch, 8, tid, increment=False), 0, tid,
                     scratch, data=values[3].to_bytes(4, "little") * 2)
        self.count("TGT_CNT_OK", "READ")
        tid = self.tid()
        self.replied("write of no data", rmap.write_command(t, scratch, b"", tid), 0, tid, scratch)
        self.count("TGT_CNT_OK", "WRITE")
        tid = self.tid()
        self.replied("read of no data", rmap.read_command(t, scratch, 0, tid), 0, tid, scratch, data=b"")
        self.count("TGT_CNT_OK", "READ")
        self.value(scratch, values[3], "the write of no data")
        self.check_last("read back")
        for words in (2, 3):
            tid = self.tid()
            packet = rmap.command(t, READ, scratch, 4, tid, ra_words=words)
            self.replied(f"read with a Reply Address field of {4 * words} bytes", packet, 0, tid, scratch,
                         data=values[3].to_bytes(4, "little"))
            self.count("TGT_CNT_OK", "READ")
        # read-modify-write of IRQ_ENA of the ECC monitor: bit 1 set, bit 0 kept
        self.write(self.irq_ena, 0x1)
        tid = self.tid()
        packet = rmap.rmw_command(t, self.irq_ena, (0x2).to_bytes(4, "little"), (0x2).to_bytes(4, "little"), tid)
        self.replied("read-modify-write of IRQ_ENA", packet, 0, tid, self.irq_ena, data=(0x1).to_bytes(4, "little"))
        self.count("TGT_CNT_OK2", "RMW")
        self.value(self.irq_ena, 0x3, "the read-modify-write of IRQ_ENA")
        self.write(self.irq_ena, 0x0)

    def windows(self) -> None:
        """Window 3 opened for the registers of the target, window 0 restricted and restored through it."""
        t, s = self.target, self.ctx.system
        last = s.rmap_windows - 1
        before = self.regs.read_block(self.base + OMAP.WIN_CTRL(0), 3)
        self.count("TGT_CNT_OK", "READ")
        check(before[0] & WIN["ENABLE"] and before[0] & WIN["WRITE"] and not before[0] & WIN["VERIFIED_ONLY"],
              f"window 0 of {s.rmap} does not permit writes: 0x{before[0]:08X}")
        # window 3: reads and writes of the registers of the RMAP target only
        self.regs.write_block(self.base + OMAP.WIN_CTRL(last), [WIN["ENABLE"] | WIN["READ"] | WIN["WRITE"],
                                                                self.base, self.base + 0xFFFF])
        self.count("TGT_CNT_OK", "WRITE")
        self.write(self.base + OMAP.WIN_CTRL(0), before[0] | WIN["VERIFIED_ONLY"])
        tid = self.tid()
        packet = rmap.write_command(t, self.irq_ena, (0x1).to_bytes(4, "little"), tid, verify=False)
        self.replied("non-verified write with window 0 restricted to verified writes", packet, 10, tid, self.irq_ena)
        self.count("TGT_CNT_ERR5", "NOT_AUTH")
        self.value(self.irq_ena, 0x0, "the refused non-verified write")
        self.write(self.base + OMAP.WIN_CTRL(0), before[0] & ~WIN["WRITE"])
        tid = self.tid()
        packet = rmap.write_command(t, self.irq_ena, (0x1).to_bytes(4, "little"), tid)
        self.replied("verified write with window 0 without writes", packet, 10, tid, self.irq_ena)
        self.count("TGT_CNT_ERR5", "NOT_AUTH")
        # restored through window 3, which covers the registers of the target
        self.write(self.base + OMAP.WIN_CTRL(0), before[0])
        self.value(self.base + OMAP.WIN_CTRL(0), before[0], "window 0 restored through window 3")
        self.regs.write_block(self.base + OMAP.WIN_CTRL(last), [0, 0, 0])
        self.count("TGT_CNT_OK", "WRITE")
        check(self.regs.read_block(self.base + OMAP.WIN_CTRL(last), 3) == [0, 0, 0],
              f"window {last} of {s.rmap} is not closed again")
        self.count("TGT_CNT_OK", "READ")


def tc15_rmap_errors(regs: Registers, ctx: systest.Context, unmapped: int) -> None:
    """The steps of TC-RS-HW-15 (module documentation); unmapped: an address outside the register windows of the
    system."""
    cases = RmapErrors(regs, ctx, unmapped)
    cases.check_last("start")
    cases.statuses()
    cases.discards()
    cases.executions()
    cases.windows()
    s = ctx.system
    expected = {}
    for (register, counter), n in cases.counts.items():
        if register in ("TGT_CNT_OK", "TGT_CNT_OK2"):
            continue                                  # no error counters: the reads of caused() count as well
        name = f"{s.rmap}.{register}"
        expected[name] = expected.get(name, 0) | OMAP.field(register, counter, n)
    events = OMAP.field("EVENTS", "TGT_ERROR", 1) | OMAP.field("EVENTS", "TGT_DISCARD", 1) | OMAP.field(
        "EVENTS", "PKT_DISCARD", 1)
    expected[f"{s.rmap}.EVENTS"] = events
    systest.caused(regs, ctx, "TC-RS-HW-15", expected=expected)
