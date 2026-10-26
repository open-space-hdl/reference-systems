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
from refsys.systest import (EDAC_INFO, LANE_ACTIVE, LINK_INITIALISED, OCR, OCR_IV, OCR_SLOT, OFB, OMAP, SPP,
                            SPP_RX_ERRORS, Setup, System, generic_value, generic_words, loaded_slot)
from refsys.transport import FAR_END_LINK_RESET, Transport, TransportError

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
        self.drop_next = False                            # a double error waits in the receive descriptor
        self.rx_table = {}                                # APID: entry of the receive APID table
        self.tx_table = {}                                # APID: entry of the send APID table
        self.slots = {}                                   # (core, slot): [key, fixed, next counter]
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

    # --- link -------------------------------------------------------------------------------------
    def operator(self, step: str) -> None:
        """The link reset of the far end: the port of the system sets DL_ERRORS.FAR_END_LINK_RESET, its lanes start
        again and the link initialises again (OpenFibre, DL-LR-03)."""
        assert step == FAR_END_LINK_RESET
        port = self.system.base(self.system.port)
        self.regs[port + OFB.DL_ERRORS] = self.regs.get(port + OFB.DL_ERRORS, 0) | OFB.field(
            "DL_ERRORS", "FAR_END_LINK_RESET", 1)

    def _lane_control(self, lane: int, value: int) -> None:
        """A lane without TX_EN and RX_EN is held in reset and leaves the link, which realigns on the other lanes
        (ML_MISALIGNED counts); with both again it joins the link (OpenFibre, ML-LM-12, ML-AL-09)."""
        port = self.system.base(self.system.port)
        on = OFB.get("LANE_CTRL", "TX_EN", value) and OFB.get("LANE_CTRL", "RX_EN", value)
        self.regs[port + OFB.LANE_STATUS(lane)] = OFB.field("LANE_STATUS", "STATE", LANE_ACTIVE if on else 0)
        ml = self.regs[port + OFB.ML_STATUS]
        bits = OFB.field("ML_STATUS", "DATA_SENDING", 1 << lane) | OFB.field("ML_STATUS", "DATA_RECEIVING", 1 << lane)
        if bool(ml & bits) != bool(on):
            self.regs[port + OFB.ML_STATUS] = ml | bits if on else ml & ~bits
            self._count(port + OFB.ML_MISALIGNED)

    # --- memory errors -----------------------------------------------------------------------------
    def _ecc_count(self, core: str, channel: int, double: bool) -> None:
        """One error counted by the monitor channel of a core (ECC_COUNT through ECC_SELECT): SEC in bits 15:0, DED in
        bits 31:16; the DED flag of the channel in ECC_STATUS."""
        r = self.error_items[f"{core}.ECC_COUNT({channel})"]
        key = (r.address, channel)
        self.ecc[key] = self.ecc.get(key, 0) + (1 << 16 if double else 1)
        status = self.error_items[f"{core}.ECC_STATUS"].address
        if double:
            self.regs[status] = self.regs.get(status, 0) | 1 << channel

    def _inject(self, address: int, value: int) -> bool:
        """ECC_INJECT of the SpaceFibre port, the RMAP target and the Space Packet node: the error is counted at once,
        as the traffic of the test case writes the buffer, with the outcome of a double error that the test sees
        (refsys.ecctest). Returns whether the address is one of them."""
        s = self.system
        if address == s.base(s.port) + OFB.ECC_INJECT:
            channel, double = OFB.get("ECC_INJECT", "CHANNEL", value), OFB.get("ECC_INJECT", "DOUBLE", value)
            per_vc = s.num_vcs if channel in (0, 3) and not double else 1
            for _ in range(per_vc):
                self._ecc_count(s.port, channel, bool(double))
            status = s.base(s.port) + OFB.ECC_STATUS
            if not double:
                self.regs[status] = self.regs.get(status, 0) | OFB.field("ECC_STATUS", "SEC", 1)
            elif channel in (6, 7):
                self._count(s.base(s.port) + OFB.DL_RETRIES)
            return True
        if address == s.base(s.rmap) + OMAP.ECC_INJECT:
            channel = OMAP.get("ECC_INJECT", "CHANNEL", value)
            double = bool(OMAP.get("ECC_INJECT", "DOUBLE", value))
            if double or OMAP.get("ECC_INJECT", "SINGLE", value):
                self._ecc_count(s.rmap, channel, double)
                events = s.base(s.rmap) + OMAP.EVENTS
                self.regs[events] = self.regs.get(events, 0) | OMAP.field("EVENTS", "ECC_DED" if double else "ECC_SEC", 1)
                self.target.write_error = double and channel in (0, 1)
            return True
        if address == s.base(s.node) + SPP.ECC_INJECT:
            channel, rx = SPP.get("ECC_INJECT", "CHANNEL", value), SPP.get("ECC_INJECT", "RX", value)
            double = bool(SPP.get("ECC_INJECT", "DOUBLE", value))
            if double or SPP.get("ECC_INJECT", "SINGLE", value):
                sends = len(s.channels)
                monitor = (4 + channel if rx else 0 if channel < 2 * sends and channel % 2 == 0
                           else 1 if channel < 2 * sends else 2)
                self._ecc_count(s.node, monitor, double)
                events = s.base(s.node) + SPP.EVENTS
                self.regs[events] = self.regs.get(events, 0) | SPP.field("EVENTS", "ECC_DED" if double else "ECC_SEC", 1)
                self.drop_next = double and rx and channel == 1
            return True
        return False

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
            return self._rx_entry(self.regs.get(node + SPP.RX_TABLE_APID, 0))
        if address == node + SPP.TX_TABLE_ENTRY:
            return self._tx_entry(self.regs.get(node + SPP.TX_TABLE_APID, 0))
        return self.regs.get(address, 0)

    def _write(self, address, value):
        node = self.system.base(self.system.node)
        if self._inject(address, value):
            return
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
        port = self.system.base(self.system.port)
        for lane in range(self.system.lanes):
            if address == port + OFB.LANE_CTRL(lane):
                self._lane_control(lane, value)
        if address == node + SPP.RX_TABLE_ENTRY:          # the raw entry, LAST and VALID included
            self.rx_table[self.regs.get(node + SPP.RX_TABLE_APID, 0)] = value
        elif address == node + SPP.TX_TABLE_ENTRY:
            self.tx_table[self.regs.get(node + SPP.TX_TABLE_APID, 0)] = value
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

    def _rx_entry(self, apid: int) -> int:
        """The entry of the receive APID table: as written, else the path default (enabled, to port 0)."""
        return self.rx_table.get(apid, SPP.field("RX_TABLE_ENTRY", "ENABLE", 1))

    def _tx_entry(self, apid: int) -> int:
        """The entry of the send APID table: as written or counted, else the path default (enabled, to destination
        0, count 0)."""
        return self.tx_table.get(apid, SPP.field("TX_TABLE_ENTRY", "ENABLE", 1))

    def _loss(self, apid: int, entry: int, count: int) -> bool:
        """Continuity check of the receive APID table: the Data Loss Indicator of a packet of the APID; with CHECK the
        entry takes the count as its LAST and becomes VALID (OpenSPP user guide 4.2)."""
        if not SPP.get("RX_TABLE_ENTRY", "CHECK", entry):
            return False
        valid, last = SPP.get("RX_TABLE_ENTRY", "VALID", entry), SPP.get("RX_TABLE_ENTRY", "LAST", entry)
        keep = entry & ~(SPP.field("RX_TABLE_ENTRY", "LAST", 0x3FFF) | SPP.field("RX_TABLE_ENTRY", "VALID", 1))
        self.rx_table[apid] = keep | SPP.field("RX_TABLE_ENTRY", "LAST", count) | SPP.field("RX_TABLE_ENTRY", "VALID", 1)
        return bool(valid) and count != (last + 1) & 0x3FFF

    def _discard(self, code: int, port: int = 0, apid: int = 0) -> list[bytes]:
        """A Space Packet that the node discards: its counter, LAST_ERROR and EVENTS.RX_DISCARD (OpenSPP register
        map)."""
        node = self.system.base(self.system.node)
        register, counter = SPP_RX_ERRORS[code]
        address = node + getattr(SPP, register)
        shift, mask = getattr(SPP, f"{register}_{counter}_SHIFT"), getattr(SPP, f"{register}_{counter}_MASK")
        word = self.regs.get(address, 0)
        self.regs[address] = word & ~(mask << shift) | min((word >> shift & mask) + 1, mask) << shift
        self.regs[node + SPP.LAST_ERROR] = (SPP.field("LAST_ERROR", "CODE", code) | SPP.field("LAST_ERROR", "PORT", port)
                                            | SPP.field("LAST_ERROR", "APID", apid))
        self.regs[node + SPP.EVENTS] = self.regs.get(node + SPP.EVENTS, 0) | SPP.field("EVENTS", "RX_DISCARD", 1)
        return []

    def _spp(self, packet):
        """The receive checks of the Space Packet node in their order (OpenSPP specification, decapsulation, receive
        buffer and demultiplexer; RX_STRIP 0, TLA_CHECK 0), then the processing of the channel."""
        node = self.system.base(self.system.node)
        if self.drop_next:
            self.drop_next = False
            return self._discard(0x1B)
        if len(packet) <= 4:
            return self._discard(0x14)
        if packet[1] != 0x02:
            return self._discard(0x11)
        if packet[2] != 0:
            return self._discard(0x12)
        user_application, payload = packet[3], bytes(packet[4:])
        head = (payload[:2] + b"\x00\x00")[:2]
        apid = int.from_bytes(head, "big") & 0x7FF
        if len(payload) < 7:
            return self._discard(0x14, apid=apid)
        if payload[0] >> 5:
            return self._discard(0x15, apid=apid)
        if len(payload) != int.from_bytes(payload[4:6], "big") + 7:
            return self._discard(0x16, apid=apid)
        if len(payload) > min(self.regs.get(node + SPP.MAX_LENGTH, 0), self.system.rx_buffer_bytes):
            return self._discard(0x17, apid=apid)
        if apid == 0x7FF and not SPP.get("CONFIG", "IDLE_DELIVER", self.regs.get(node + SPP.CONFIG, 0)):
            return self._discard(0x18, apid=apid)
        sp = SpacePacket.decode(payload)
        entry = self._rx_entry(sp.apid)
        port = SPP.get("RX_TABLE_ENTRY", "PORT", entry)
        if not SPP.get("RX_TABLE_ENTRY", "ENABLE", entry) or port >= len(self.system.channels):
            return self._discard(0x19, port, sp.apid)
        if sp.sequence_flags != 0b11:
            return self._discard(0x1A, port, sp.apid)
        ch = self.system.channels[port]
        self._count(node + SPP.RX_PACKETS)
        loss = self._loss(sp.apid, entry, sp.sequence_count)
        if loss:
            self._count(node + SPP.RX_LOSS)
        out = []
        for data in self.process(ch, sp.data, user_application, loss):
            for data_field in self.send(ch, data):
                tx = self._tx_entry(ch.output_apid)
                seq = SPP.get("TX_TABLE_ENTRY", "COUNT", tx)
                count = SPP.field("TX_TABLE_ENTRY", "COUNT", 0x3FFF)
                self.tx_table[ch.output_apid] = tx & ~count | SPP.field("TX_TABLE_ENTRY", "COUNT", (seq + 1) & 0x3FFF)
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
