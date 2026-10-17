"""Software model of a reference system as seen from the far end of its link, for the tests of the hardware tests
and the stimuli of the system simulations.

The model answers RMAP commands from a register model and turns Space Packets into authenticated chunks as the
Space Packet node, the framers and the AES-GCM cores do; a system adds what its channels do with the Packet Data
Field of an input Space Packet before the framer (process()). It checks the test procedure and its use of the
libraries before a test on hardware; it is not a model of the RTL. Register addresses and fields come from the same
headers as the test (cores/<core>/sw/<prefix>_regs.h); the behaviour follows these documents of the pinned cores, so
a change of a core that the model does not follow shows up when the test runs on hardware or in the system
simulation:

- RMAP target: replies of ECSS-E-ST-50-52C 5.3.2 and 5.4.2; commands of several words with incrementing addresses
  (5.3.1, 5.4.1); the key is checked after the reset and a wrong key gives the status "invalid key" (OpenRMAP,
  specification of omap_target TG-AU-04, register TGT_KEY.KEY_EN reset 1); data bytes in little-endian order
  (OpenRMAP user guide, endian order).
- SpaceFibre port: identification and the states of an initialised link and of active lanes (OpenFibre register map,
  DL_STATUS and LANE_STATUS; user guide, programming sequence).
- Space Packet node: every APID goes to receive port 0 after the reset (RxPathDefault_g, OpenSPP user guide,
  generics); the receive APID table is accessed through RX_TABLE_APID and RX_TABLE_ENTRY (user guide, programming
  sequence); with RX_TABLE_ENTRY.CHECK the Packet Sequence Count of an APID is checked, a count that is not the last
  one plus one sets the Data Loss Indicator of the SDU and is counted in RX_LOSS, the first packet after the check is
  enabled sets none (user guide 4.2); the receive ports deliver the Packet Data Field with the User Application value
  of the CPTP packet and the send ports build the Packet Primary Header with the APID of their side band and a
  sequence count per APID (Octet String Service, user guide 4.1 and 4.2); destination 0 has the Target Logical
  Address 0xFE (reset value of DEST_CTRL); counters RX_PACKETS and TX_PACKETS.
- Framer and AES-GCM core: chunks of ChunkBytes_g bytes with their position (OpenCrypto, architecture of ocr_framer,
  section 2), key words with byte 4 n in bits 31:24, KEY_LOAD with START, IV_GEN and the slot, the load clears the
  key staging registers, IV_FIXED and IV_COUNT, the query of a slot through SLOT_QUERY and SLOT_STATUS to
  SLOT_COUNT_HI (user guide, programming sequence; register map), the dense output IV, position, ciphertext, tag
  (user guide 4.6), one invocation per chunk, counter ENCRYPTIONS.
- ECC monitor: INFO with the channel count and the counter width (Open Logic FT, olo_ft_ecc_monitor_axi).
"""

from refsys import chunks, rmap
from refsys.composer import SystemFile
from refsys.spp import SpacePacket, cptp_decode, cptp_encode
from refsys.systest import (EDAC_INFO, LANE_ACTIVE, LINK_INITIALISED, OCR, OCR_IV, OCR_SLOT, OFB, OMAP, SPP, Setup,
                            System, loaded_slot)
from refsys.transport import Transport, TransportError

SPECIFIED = (False, False)                    # reads and writes of several words as specified


class SystemModel(Transport):
    """A reference system as seen from the far end of its link.

    first_word: whether a read and whether a write of several words use the address of their first word for every
    word, as with the defect of olo_axi_master_simple in Open Logic FT up to 4.7.0-ft.4. corrupt: every chunk leaves
    with one ciphertext bit inverted.
    """

    def __init__(self, system: System, system_file: SystemFile, setup: Setup, corrupt: bool = False,
                 first_word: tuple[bool, bool] = SPECIFIED):
        s = self.system = system
        self.setup = setup
        self.corrupt = corrupt
        self.first_word_read, self.first_word_write = first_word
        self.key = 0x00                                   # Key_g
        port, node = s.base(s.port), s.base(s.node)
        self.regs = {port + OFB.ID: OFB.REGMAP_ID, port + OFB.DL_STATUS: LINK_INITIALISED,
                     node + SPP.ID: SPP.REGMAP_ID, s.base(s.rmap) + OMAP.ID: OMAP.REGMAP_ID,
                     s.base(s.ecc) + EDAC_INFO: s.ecc_info}
        for lane in range(s.lanes):
            self.regs[port + OFB.LANE_STATUS(lane)] = LANE_ACTIVE
        for ch in s.channels:
            core = s.base(ch.core)
            self.regs[core + OCR.ID] = OCR.REGMAP_ID
            self.regs[core + OCR.GENERICS] = OCR.field("GENERICS", "NUM_KEYS", ch.key_slots)
            self.regs[core + OCR.RELEASE_BYTES] = system_file.generic(ch.core, "ReleaseBytes_g")
        self.rx_table = {}
        self.slots = {}                                   # (core, slot): [key, fixed, next counter]
        self.seq = {}
        self.last = {}                                    # APID: last Packet Sequence Count (continuity check)

    # --- RMAP target -------------------------------------------------------------------------------
    def _rmap(self, packet):
        assert packet[:len(self.setup.rmap_path)] == self.setup.rmap_path
        cmd = packet[len(self.setup.rmap_path):]          # the routing switch discards the path address
        instr, key = cmd[2], cmd[3]
        ra = 4 * (instr & 3)
        header = cmd[:4 + ra + 12]
        assert rmap.crc8(header) == 0
        reply_path = bytes(cmd[4:4 + ra]).lstrip(b"\x00")
        assert reply_path == self.setup.rmap_reply_path
        ila, tid = header[4 + ra], (header[5 + ra] << 8) | header[6 + ra]
        address = int.from_bytes(header[8 + ra:12 + ra], "big")
        length = int.from_bytes(header[12 + ra:15 + ra], "big")
        write = bool(instr & 0x20)
        assert instr & 0x04 and address % 4 == 0 and length % 4 == 0  # incrementing, whole registers
        first_word = self.first_word_write if write else self.first_word_read
        status = 0 if key == self.key else 3
        data = b""
        if write and status == 0:
            assert rmap.crc8(cmd[len(header):]) == 0
            values = cmd[len(header):len(header) + length]
            for n in range(0, length, 4):
                self._write(address + (0 if first_word else n), int.from_bytes(values[n:n + 4], "little"))
        elif status == 0:
            values = [self._read(address + (0 if first_word else n)) for n in range(0, length, 4)]
            data = b"".join(v.to_bytes(4, "little") for v in values)
        out = bytes([ila, 0x01, instr & 0x3F, status, cmd[0], tid >> 8, tid & 0xFF])
        if not write:
            out += bytes([0]) + len(data).to_bytes(3, "big")
        out += bytes([rmap.crc8(out)])
        return out + data + bytes([rmap.crc8(data)]) if not write else out

    def _read(self, address):
        node = self.system.base(self.system.node)
        if address == node + SPP.RX_TABLE_ENTRY:
            return self.rx_table.get(self.regs.get(node + SPP.RX_TABLE_APID, 0), 0)
        return self.regs.get(address, 0)

    def _write(self, address, value):
        node = self.system.base(self.system.node)
        self.regs[address] = value
        if address == node + SPP.RX_TABLE_ENTRY:
            apid = self.regs.get(node + SPP.RX_TABLE_APID, 0)
            self.rx_table[apid] = value
            self.last.pop(apid, None)                     # the next packet of the APID sets no loss indicator
        for ch in self.system.channels:
            core = self.system.base(ch.core)
            if address == core + OCR.KEY_LOAD:
                slot = OCR.get("KEY_LOAD", "SLOT", value)
                assert OCR.get("KEY_LOAD", "START", value) and OCR.get("KEY_LOAD", "IV_GEN", value)
                assert slot < ch.key_slots
                staging = [core + OCR.KEY(n) for n in range(8)] + [core + getattr(OCR, r) for r in OCR_IV]
                key = b"".join(self.regs.get(a, 0).to_bytes(4, "big") for a in staging[:8])
                counter = (self.regs.get(core + OCR.IV_COUNT_HI, 0) << 32) | self.regs.get(
                    core + OCR.IV_COUNT_LO, 0)
                self.slots[(core, slot)] = [key, self.regs.get(core + OCR.IV_FIXED, 0), counter]
                for a in staging:
                    self.regs[a] = 0                      # the load clears the staging registers
            elif address == core + OCR.SLOT_QUERY:
                state = [0, 0, 0, 0]
                if (core, value) in self.slots:
                    _, fixed, counter = self.slots[(core, value)]
                    state = loaded_slot(fixed, counter)
                for r, v in zip(OCR_SLOT, state):
                    self.regs[core + getattr(OCR, r)] = v

    def _count(self, address, n=1):
        self.regs[address] = self.regs.get(address, 0) + n

    # --- Space Packets -----------------------------------------------------------------------------
    def process(self, channel, data: bytes, user_application: int, loss: bool) -> list[bytes]:
        """The packets that a channel gives its framer for the Packet Data Field of one input Space Packet, with the
        User Application value of its CPTP packet and its Data Loss Indicator."""
        raise NotImplementedError

    def _loss(self, apid: int, entry: int, count: int) -> bool:
        """Continuity check of the receive APID table: the Data Loss Indicator of a packet of the APID."""
        if not SPP.get("RX_TABLE_ENTRY", "CHECK", entry):
            return False
        last = self.last.get(apid)
        self.last[apid] = count
        return last is not None and count != (last + 1) & 0x3FFF

    def _spp(self, packet):
        user_application, payload = cptp_decode(packet, self.setup.system_tla)
        sp = SpacePacket.decode(payload)
        node = self.system.base(self.system.node)
        entry = self.rx_table.get(sp.apid, SPP.field("RX_TABLE_ENTRY", "ENABLE", 1))
        ch = self.system.channels[SPP.get("RX_TABLE_ENTRY", "PORT", entry)]
        self._count(node + SPP.RX_PACKETS)
        loss = self._loss(sp.apid, entry, sp.sequence_count)
        if loss:
            self._count(node + SPP.RX_LOSS)
        slot = self.slots[(self.system.base(ch.core), ch.slot)]
        key, fixed, counter = slot
        out = []
        for data in self.process(ch, sp.data, user_application, loss):
            for pos, part in chunks.split(data, self.system.chunk_bytes):
                chunk = bytearray(chunks.encrypt(key, fixed, counter, pos, part))
                if self.corrupt:
                    chunk[20] ^= 1
                counter += 1
                slot[2] = counter
                seq = self.seq.get(ch.output_apid, 0)
                self.seq[ch.output_apid] = (seq + 1) & 0x3FFF
                out.append(cptp_encode(SpacePacket(apid=ch.output_apid, data=bytes(chunk),
                                                   sequence_count=seq).encode(), self.setup.far_end_tla))
                self._count(self.system.base(ch.core) + OCR.ENCRYPTIONS)
        self._count(node + SPP.TX_PACKETS, len(out))
        return out

    def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
        assert tx_channel == rx_channel
        if tx_channel == self.setup.rmap_channel:
            out = [self._rmap(p) for p in packets]
        else:
            assert tx_channel == self.setup.spp_channel
            out = [o for p in packets for o in self._spp(p)]
        if len(out) < count:
            raise TransportError(f"{len(out)} of {count} packets")
        return out[:count]
