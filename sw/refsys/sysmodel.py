"""Software model of a reference system as seen from the far end of its link, for the tests of the hardware tests
and the stimuli of the system simulations.

The model answers RMAP commands from a register model and turns Space Packets into authenticated chunks as the
Space Packet node, the framers and the AES-GCM cores do; a system adds what its channels do with the Packet Data
Field of an input Space Packet before the framer (process()), and a system without framers and AES-GCM cores the
Packet Data Fields that a channel sends instead of the chunks (send()). It checks the test procedure and its use of the
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

from refsys import chunks, errorstate, rmap
from refsys.composer import SystemFile
from refsys.rmapmodel import RmapTargetModel
from refsys.spp import SpacePacket, cptp_decode, cptp_encode
from refsys.systest import (EDAC_INFO, LANE_ACTIVE, LINK_INITIALISED, OCR, OCR_IV, OCR_SLOT, OFB, OMAP, SPP, Setup,
                            System, generic_value, generic_words, loaded_slot)
from refsys.transport import Transport, TransportError

SPECIFIED = (False, False)                    # reads and writes of several words as specified

# Window 0 of the RMAP target after the reset without WinInit_g: enabled for reads, writes, RMW and single-address
# accesses to the whole address range (OpenRMAP register map, WIN_CTRL)
WINDOW0 = (0x00FF0037, 0x00000000, 0xFFFFFFFF)


def reset_values(s: System, f: SystemFile) -> dict[int, int]:
    """The registers of the SpaceFibre port, the RMAP target and the Space Packet node that hold a value other than 0
    after the reset of an initialised link, by address (register maps of the cores)."""
    out = {}
    for instance, words in generic_words(f, s).items():
        regmap = {s.port: OFB, s.rmap: OMAP, s.node: SPP}[instance]
        out |= {s.base(instance) + getattr(regmap, name): value for name, value in words.items()}
    port, ofb = s.base(s.port), f.generics(s.port)
    lanes = (1 << s.lanes) - 1
    out |= {port + OFB.DL_CTRL: OFB.field("DL_CTRL", "DATA_SCRAMBLED", 1), port + OFB.DL_BC_INTERVAL: 0x28,
            port + OFB.DL_STATUS: OFB.field("DL_STATUS", "LINK_STATE", LINK_INITIALISED)
            | OFB.field("DL_STATUS", "TX_READY", 1),
            port + OFB.ML_STATUS: OFB.field("ML_STATUS", "ALIGN_STATE", 2) | OFB.field("ML_STATUS", "DATA_RECEIVING", lanes)
            | OFB.field("ML_STATUS", "DATA_SENDING", lanes),
            port + OFB.VC_HAS_CREDIT: (1 << s.num_vcs) - 1, port + OFB.VC_IDLE_LIMIT: 0x2625A,
            port + OFB.ML_CTRL: 0x004, port + OFB.LANE_INIT_TIMEOUT: ofb["InitTimeoutWords_g"],
            port + OFB.PA_CTRL: 0x03600005}
    for lane in range(s.lanes):
        out[port + OFB.LANE_CTRL(lane)] = 0x62 | generic_value(ofb["LaneStart_g"])
        out[port + OFB.LANE_STATUS(lane)] = OFB.field("LANE_STATUS", "STATE", LANE_ACTIVE)
    for vc in range(s.num_vcs):
        out[port + OFB.VC_CFG(vc)] = OFB.field("VC_CFG", "VN", vc) | OFB.field("VC_CFG", "PRIORITY", 3)
        out[port + OFB.VC_BANDWIDTH(vc)] = 0x0A00 if vc == 0 else 0xFFFF
        out[port + OFB.VC_SLOTS_LO(vc)] = out[port + OFB.VC_SLOTS_HI(vc)] = 0xFFFFFFFF
    target, omap = s.base(s.rmap), f.generics(s.rmap)
    out |= {target + OMAP.TGT_LA: generic_value(omap["La0_g"]) | generic_value(omap["La1_g"]) << 8 | 1 << 16
            | OMAP.field("TGT_LA", "DEF_LA_EN", generic_value(omap["DefLaEn_g"])),
            target + OMAP.TGT_KEY: OMAP.field("TGT_KEY", "KEY_EN", 1) | generic_value(omap["Key_g"])}
    if omap.get("WinInit_g") is None and omap["Windows_g"]:
        for address, value in zip((OMAP.WIN_CTRL(0), OMAP.WIN_BASE(0), OMAP.WIN_LAST(0)), WINDOW0):
            out[target + address] = value
    node, spp = s.base(s.node), f.generics(s.node)
    out |= {node + SPP.CONFIG: generic_value(spp["RoundRobin_g"]) | generic_value(spp["Tla_g"]) << 8,
            node + SPP.MAX_LENGTH: spp["MaxLength_g"], node + SPP.SCRUB: 1}
    for dest in range(spp["NumDest_g"]):
        out[node + SPP.DEST_CTRL(dest)] = SPP.field("DEST_CTRL", "TLA", 0xFE)
    return out


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
        port, node = s.base(s.port), s.base(s.node)
        self.regs = {port + OFB.ID: OFB.REGMAP_ID, port + OFB.DL_STATUS: LINK_INITIALISED,
                     node + SPP.ID: SPP.REGMAP_ID, s.base(s.rmap) + OMAP.ID: OMAP.REGMAP_ID,
                     s.base(s.ecc) + EDAC_INFO: s.ecc_info}
        for lane in range(s.lanes):
            self.regs[port + OFB.LANE_STATUS(lane)] = LANE_ACTIVE
        for ch in s.cores():
            core = s.base(ch.core)
            self.regs[core + OCR.ID] = OCR.REGMAP_ID
            self.regs[core + OCR.GENERICS] = OCR.field("GENERICS", "NUM_KEYS", ch.key_slots)
            self.regs[core + OCR.RELEASE_BYTES] = system_file.generic(ch.core, "ReleaseBytes_g")
        self.regs |= reset_values(s, system_file)
        self.rx_table = {}
        self.slots = {}                                   # (core, slot): [key, fixed, next counter]
        self.seq = {}
        self.last = {}                                    # APID: last Packet Sequence Count (continuity check)
        # how the error registers clear (refsys.errorstate): write-one-to-clear bits, any write, the writes that clear
        # the memory error counters, and the counters read through a select register
        items = s.error_registers()
        self.w1c = {r.address: r.clear for r in items if isinstance(r, errorstate.Register) and r.clear}
        self.rc = {r.address for r in items if isinstance(r, errorstate.Register) and r.clear == 0}
        self.selected = {r.address: r.select[0] for r in items if isinstance(r, errorstate.Register) and r.select}
        self.ecc = {}                                     # (address of ECC_COUNT, channel): counter word
        self.clears = {c.address: c for c in items if isinstance(c, errorstate.Clear)}
        rmap0 = s.base(s.rmap)
        self.target = RmapTargetModel(system_file.generics(s.rmap), OMAP, rmap0, self._bus_read, self._bus_write,
                                      lambda a: self.regs.get(a, 0), self.regs.__setitem__,
                                      first_word_read=self.first_word_read, first_word_write=self.first_word_write)
        self.windows = [s.base(name) for name in s.windows]
        decoder = system_file.instances_of("olo/olo_axi_lite_decoder")[0]
        self.window_bytes = 1 << system_file.generic(decoder, "PortAddrWidth_g")
        self.error_items = {r.name: r for r in items if isinstance(r, errorstate.Register)}
        # bits that read 0 whatever is written: the VN of VC 0 (OpenFibre register map, VC_CFG, MG-VN-01)
        self.zero_bits = {port + OFB.VC_CFG(0): OFB.field("VC_CFG", "VN", OFB.VC_CFG_VN_MASK)}

    # --- RMAP target -------------------------------------------------------------------------------
    def _rmap(self, packet) -> bytes | None:
        """The reply of the RMAP target (refsys.rmapmodel) to a command, without the reply address that the routing
        switch of the far end takes; None without a reply."""
        assert packet[:len(self.setup.rmap_path)] == self.setup.rmap_path
        reply = self.target.packet(bytes(packet[len(self.setup.rmap_path):]))  # the switch takes the path address
        if reply is None:
            return None
        assert reply.startswith(self.setup.rmap_reply_path), reply[:4].hex()
        return reply[len(self.setup.rmap_reply_path):]

    def _bus_read(self, address: int) -> int | None:
        """A read of the AXI master of the RMAP target: None (DECERR) outside the register windows of the system."""
        return self._read(address) if self._decoded(address) else None

    def _bus_write(self, address: int, value: int) -> bool:
        if not self._decoded(address):
            return False
        self._write(address, value)
        return True

    def _decoded(self, address: int) -> bool:
        return any(base <= address < base + self.window_bytes for base in self.windows)

    def _clear_ecc(self, clear: errorstate.Clear) -> None:
        """A write of ECC_STATUS (or CTRL.CLR_ALL of the ECC monitor): the registers it clears read 0."""
        for name in clear.clears:
            r = self.error_items[name]
            if r.select:
                self.ecc[(r.address, r.select[1])] = 0
            else:
                self.regs[r.address] = 0

    def _read(self, address):
        node = self.system.base(self.system.node)
        if address in self.selected:
            return self.ecc.get((address, self.regs.get(self.selected[address], 0)), 0)
        if address == node + SPP.RX_TABLE_ENTRY:
            return self.rx_table.get(self.regs.get(node + SPP.RX_TABLE_APID, 0), 0)
        return self.regs.get(address, 0)

    def _write(self, address, value):
        node = self.system.base(self.system.node)
        if address in self.w1c:
            self.regs[address] = self.regs.get(address, 0) & ~(value & self.w1c[address])
            return
        if address in self.rc:
            self.regs[address] = 0
            return
        if address in self.clears:
            self._clear_ecc(self.clears[address])
            return
        self.regs[address] = value & ~self.zero_bits.get(address, 0)
        if address == node + SPP.RX_TABLE_ENTRY:
            apid = self.regs.get(node + SPP.RX_TABLE_APID, 0)
            self.rx_table[apid] = value
            self.last.pop(apid, None)                     # the next packet of the APID sets no loss indicator
        for ch in self.system.cores():
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
        out = []
        for data in self.process(ch, sp.data, user_application, loss):
            for data_field in self.send(ch, data):
                seq = self.seq.get(ch.output_apid, 0)
                self.seq[ch.output_apid] = (seq + 1) & 0x3FFF
                packet = SpacePacket(apid=ch.output_apid, data=data_field, sequence_count=seq)
                out.append(cptp_encode(packet.encode(), self.setup.far_end_tla))
        self._count(node + SPP.TX_PACKETS, len(out))
        return out

    def send(self, channel, data: bytes) -> list[bytes]:
        """The Packet Data Fields of the Space Packets that a channel sends for one packet of its processing: here
        the authenticated chunks of its framer and AES-GCM core; a system without them gives its own."""
        slot = self.slots[(self.system.base(channel.core), channel.slot)]
        key, fixed, counter = slot
        out = []
        for pos, part in chunks.split(data, self.system.chunk_bytes):
            chunk = bytearray(chunks.encrypt(key, fixed, counter, pos, part))
            if self.corrupt:
                chunk[20] ^= 1
            counter += 1
            slot[2] = counter
            out.append(bytes(chunk))
            self._count(self.system.base(channel.core) + OCR.ENCRYPTIONS)
        return out

    def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
        assert tx_channel == rx_channel
        if tx_channel == self.setup.rmap_channel:
            out = [r for r in (self._rmap(p) for p in packets) if r is not None]
        else:
            assert tx_channel == self.setup.spp_channel
            out = [o for p in packets for o in self._spp(p)]
        if len(out) < count:
            raise TransportError(f"{len(out)} of {count} packets")
        return out[:count]
