# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Model of the RMAP target of OpenRMAP (omap_core with Target_g only, Passthrough_g false) behind its N-Char bridge,
as seen from the far end: the reply of every command, its status and what the management registers count, for the
tests of the hardware tests (sw/refsys/sysmodel.py). It follows these documents of the pinned OpenRMAP:

- the routing of omap_core: a packet of fewer than two characters or with a Protocol Identifier other than 1 is
  discarded and counted in PKT_CNT.DISCARDED (CO-DM-01/02, EVENTS.PKT_DISCARD);
- the checks of the target (specification of omap_target): a reply packet (packet type 0b00) is dropped and counted in
  TGT_CNT_ERR6.REPLY_RX (TG-RX-06), a header that ends early in TGT_CNT_HDR.HDR_SHORT (TG-RX-03) and a header CRC error
  in TGT_CNT_HDR.HDR_CRC (TG-RX-02), all without a reply (EVENTS.TGT_DISCARD); a reserved packet type gives the status
  "unused packet type or command code" without a reply (TG-AU-01); the header checks in the order of TG-AU-08:
  command code (2), target logical address (12), key (3), RMW data length (11), verify buffer (9), authorisation of
  the access by the windows (10, TG-AU-07); then the end of the packet: early EOP (5), too much data (6), data CRC
  (4) (TG-RX-05); an AXI response other than OKAY gives the status 1 (TG-EX-06);
- the execution: verified writes only after the data CRC, non-verified writes as the data arrives (TG-EX-01/02),
  incrementing and single-address accesses (TG-EX-03/04), read-modify-write with data and mask (TG-EX-05), zero
  length without an access (TG-EX-07);
- the replies (TG-TX-01..04): the Reply Address without its leading 0x00 bytes in front, the write reply for a write
  and an invalid command code, the read reply with Data Length 0 and Data CRC 0x00 for a read or RMW that fails;
- the management registers (OpenRMAP register map): TGT_LA, TGT_KEY and the windows at their reset values unless
  written, TGT_LAST and its TID and address words, the counters TGT_CNT_* (16 bits, saturating), EVENTS.

A read that ends with an AXI error ends its reply with an EEP, which the transport of the test cannot see: the model
does not give such a reply (the tests do not send such a read).
"""

from dataclasses import dataclass, field

from refsys import rmap
from refsys.regs import RegisterMap

# packet type of the instruction (bits 7:6), command code (bits 5:2), Reply Address length (bits 1:0)
REPLY_TYPE, COMMAND_TYPE = 0b00, 0b01
WRITE, VERIFY, REPLY, INCREMENT = 8, 4, 2, 1
READ_CODES = (0b0010, 0b0011)
RMW_CODE = 0b0111
RMW_LENGTHS = (0, 2, 4, 6, 8)
# header bytes after the Reply Address: ILA, TID (2), Extended Address, address (4), Data Length (3), Header CRC
HEADER_TAIL = 12


@dataclass
class Indication:
    """What the target indicates for a command with an intact header (TGT_LAST and the counters)."""

    status: int
    instruction: int
    ila: int
    tid: int
    address: int
    extended: int
    replied: bool


@dataclass
class RmapTargetModel:
    """The RMAP target: generics of omap_core (Composer file); the register access of the system behind its AXI
    master, read(address) (None for an AXI error) and write(address, value) (False for an AXI error); its management
    registers at base of the register map regmap, which the model keeps in the registers of the system: peek(address)
    and poke(address, value) access them without the effects of an access over the bus."""

    generics: dict
    regmap: RegisterMap
    base: int
    read: object
    write: object
    peek: object
    poke: object
    first_word_read: bool = False
    first_word_write: bool = False
    indications: list[Indication] = field(default_factory=list)

    def mib(self, name: str) -> int:
        if "(" in name:
            reg, index = name[:-1].split("(")
            return self.peek(self.base + getattr(self.regmap, reg)(int(index)))
        return self.peek(self.base + getattr(self.regmap, name))

    def mib_write(self, name: str, value: int) -> None:
        self.poke(self.base + getattr(self.regmap, name), value)

    def mib_count(self, register: str, counter: str, event: bool = False) -> None:
        """Counts in a 16-bit saturating counter (a 32-bit one for PKT_CNT.USER and DISCARDED as well), or sets an
        event bit."""
        address = self.base + getattr(self.regmap, register)
        word = self.peek(address)
        shift = getattr(self.regmap, f"{register}_{counter}_SHIFT")
        mask = getattr(self.regmap, f"{register}_{counter}_MASK")
        if event:
            self.poke(address, word | mask << shift)
            return
        value = min((word >> shift & mask) + 1, mask)
        self.poke(address, word & ~(mask << shift) | value << shift)

    @property
    def word_bytes(self) -> int:
        return self.generics["AxiDataWidth_g"] // 8

    def packet(self, cmd: bytes) -> bytes | None:
        """The reply to a packet that reaches the target (its path address removed), or None."""
        if len(cmd) < 2 or cmd[1] != rmap.PROTOCOL_ID:
            self.mib_count("PKT_CNT", "DISCARDED")
            self.mib_count("EVENTS", "PKT_DISCARD", event=True)
            return None
        if len(cmd) < 3:
            return self._discard("HDR", "HDR_SHORT")
        instr = cmd[2]
        if instr >> 6 == REPLY_TYPE:
            return self._discard("ERR6", "REPLY_RX")
        ra = 4 * (instr & 3)
        size = 4 + ra + HEADER_TAIL
        if len(cmd) < size:
            return self._discard("HDR", "HDR_SHORT")
        header = cmd[:size]
        if rmap.crc8(header) != 0:
            return self._discard("HDR", "HDR_CRC")
        tla, key = cmd[0], cmd[3]
        reply_address = bytes(cmd[4:4 + ra])
        ila = header[4 + ra]
        tid = (header[5 + ra] << 8) | header[6 + ra]
        extended = header[7 + ra]
        address = int.from_bytes(header[8 + ra:12 + ra], "big")
        length = int.from_bytes(header[12 + ra:15 + ra], "big")
        rest = bytes(cmd[size:])
        code = (instr >> 2) & 0xF
        kind = "write" if code & WRITE else "read" if code in READ_CODES else "rmw" if code == RMW_CODE else None

        def done(status: int, data: bytes | None = None) -> bytes | None:
            wants = bool(code & REPLY) and instr >> 6 == COMMAND_TYPE
            self._indicate(Indication(status, instr, ila, tid, address, extended, wants), kind)
            if not wants:
                return None
            return self._reply(reply_address, ila, instr, status, tla, tid, kind, data)

        if instr >> 6 != COMMAND_TYPE:                   # reserved packet type: no reply (TG-AU-01)
            self._indicate(Indication(2, instr, ila, tid, address, extended, False), kind)
            return None
        status = self._header_status(kind, tla, key, length, address, extended, code)
        if status:
            return done(status)
        # the end of the packet: data and its CRC (a write), nothing (a read)
        if kind == "read":
            if rest:
                return done(6)
            return done(*self._read(address, length, bool(code & INCREMENT)))
        if len(rest) < length + 1:
            return done(5)
        if len(rest) > length + 1:
            if kind == "write" and not code & VERIFY:
                self._write(address, rest[:length], bool(code & INCREMENT))
            return done(6)
        data = rest[:length]
        if rmap.crc8(rest) != 0:
            if kind == "write" and not code & VERIFY:
                self._write(address, data, bool(code & INCREMENT))
            return done(4)
        if kind == "rmw":
            return done(*self._rmw(address, data))
        return done(self._write(address, data, bool(code & INCREMENT)))

    # --- checks --------------------------------------------------------------------------------------------------
    def _header_status(self, kind, tla, key, length, address, extended, code) -> int:
        la = self.mib("TGT_LA")
        accepted = [la & 0xFF] if la >> 16 & 1 else []
        accepted += [la >> 8 & 0xFF] if la >> 17 & 1 else []
        accepted += [0xFE] if la >> 18 & 1 else []
        tgt_key = self.mib("TGT_KEY")
        if kind is None:
            return 2
        if tla not in accepted:
            return 12
        if tgt_key >> 8 & 1 and key != tgt_key & 0xFF:
            return 3
        if kind == "rmw" and length not in RMW_LENGTHS:
            return 11
        if kind == "write" and code & VERIFY and length > self.generics["BufferBytes_g"]:
            return 9
        return 0 if self._authorised(kind, address, extended, length, code) else 10

    def _authorised(self, kind, address, extended, length, code) -> bool:
        single = not code & INCREMENT
        w = self.word_bytes
        span = w if single else length // 2 if kind == "rmw" else length
        span = max(span, 1)
        start = (extended << 32) | address
        last = start + span - 1
        if last >= 1 << self.generics["AxiAddrWidth_g"]:
            return False
        if single and (address % w or length % w):
            return False
        if self.generics["Windows_g"] == 0:
            return True
        for n in range(self.generics["Windows_g"]):
            ctrl = self.mib(f"WIN_CTRL({n})")
            base = (ctrl >> 8 & 0xFF) << 32 | self.mib(f"WIN_BASE({n})")
            top = (ctrl >> 16 & 0xFF) << 32 | self.mib(f"WIN_LAST({n})")
            if not (base <= start and last <= top):
                continue
            allowed = {"read": ctrl >> 1 & 1,
                       "write": ctrl >> 2 & 1 and (not ctrl >> 3 & 1 or code & VERIFY),
                       "rmw": ctrl >> 4 & 1}[kind]
            return bool(ctrl & 1 and allowed and (not single or ctrl >> 5 & 1))
        return False

    # --- execution ------------------------------------------------------------------------------------------------
    def _addresses(self, address: int, length: int, increment: bool, first_word: bool = False) -> list[int]:
        """The addresses of the words of an access; first_word: every word at the address of the first one (the
        defect of olo_axi_master_simple in Open Logic FT up to 4.7.0-ft.4, for the tests of the procedure)."""
        w = self.word_bytes
        return [address + (n if increment and not first_word else 0) for n in range(0, length, w)]

    def _write(self, address: int, data: bytes, increment: bool) -> int:
        for n, a in enumerate(self._addresses(address, len(data), increment, self.first_word_write)):
            if not self.write(a, int.from_bytes(data[4 * n:4 * n + 4], "little")):
                return 1
        return 0

    def _read(self, address: int, length: int, increment: bool) -> tuple[int, bytes]:
        values = [self.read(a) for a in self._addresses(address, length, increment, self.first_word_read)]
        if any(v is None for v in values):
            raise NotImplementedError("a read with an AXI error ends its reply with an EEP")
        return 0, b"".join(v.to_bytes(4, "little") for v in values)[:length]

    def _rmw(self, address: int, data: bytes) -> tuple[int, bytes]:
        n = len(data) // 2
        if n == 0:
            return 0, b""
        old = self.read(address)
        if old is None:
            return 1, None
        old_bytes = old.to_bytes(4, "little")[:n]
        value, mask = data[:n], data[n:]
        new = bytes((v & m) | (o & ~m & 0xFF) for v, m, o in zip(value, mask, old_bytes))
        word = int.from_bytes(new + old.to_bytes(4, "little")[n:], "little")
        return (0 if self.write(address, word) else 1), old_bytes

    # --- replies, indications and counters -----------------------------------------------------------------------
    @staticmethod
    def _reply(reply_address, ila, instr, status, tla, tid, kind, data) -> bytes:
        path = reply_address.lstrip(b"\x00") or (b"\x00" if reply_address else b"")
        out = bytes([ila, rmap.PROTOCOL_ID, instr & 0x3F, status, tla, tid >> 8, tid & 0xFF])
        if kind in ("read", "rmw"):
            data = data if status == 0 or (kind == "rmw" and data) else b""
            data = data or b""
            out += bytes([0]) + len(data).to_bytes(3, "big")
            out += bytes([rmap.crc8(out)])
            return path + out + data + bytes([rmap.crc8(data)])
        return path + out + bytes([rmap.crc8(out)])

    def _discard(self, register: str, counter: str) -> None:
        self.mib_count(f"TGT_CNT_{register}", counter)
        self.mib_count("EVENTS", "TGT_DISCARD", event=True)
        return None

    def _indicate(self, ind: Indication, kind: str | None) -> None:
        self.indications.append(ind)
        if ind.status == 0:
            register, counter = {"write": ("OK", "WRITE"), "read": ("OK", "READ")}.get(kind, ("OK2", "RMW"))
            self.mib_count(f"TGT_CNT_{register}", counter)
            self.mib_count("EVENTS", "TGT_CMD", event=True)
        else:
            self.mib_count(*STATUS_COUNTERS[ind.status])
            self.mib_count("EVENTS", "TGT_ERROR", event=True)
        if ind.replied:
            self.mib_count("TGT_CNT_OK2", "REPLIES")
        self.mib_write("TGT_LAST", ind.status | ind.instruction << 8 | ind.ila << 16 | ind.replied << 24)
        self.mib_write("TGT_LAST_TID", ind.tid)
        self.mib_write("TGT_LAST_ADDR", ind.address)
        self.mib_write("TGT_LAST_ADDR_EXT", ind.extended)


# the counter of every status other than 0 (OpenRMAP register map, TGT_CNT_ERR1 to ERR6)
STATUS_COUNTERS = {1: ("TGT_CNT_ERR1", "GENERAL"), 2: ("TGT_CNT_ERR1", "UNUSED"), 3: ("TGT_CNT_ERR2", "KEY"),
                   4: ("TGT_CNT_ERR2", "DATA_CRC"), 5: ("TGT_CNT_ERR3", "EARLY_EOP"),
                   6: ("TGT_CNT_ERR3", "TOO_MUCH"), 7: ("TGT_CNT_ERR4", "EEP"), 9: ("TGT_CNT_ERR4", "VERIFY_OVERRUN"),
                   10: ("TGT_CNT_ERR5", "NOT_AUTH"), 11: ("TGT_CNT_ERR5", "RMW_LENGTH"), 12: ("TGT_CNT_ERR6", "TLA")}
