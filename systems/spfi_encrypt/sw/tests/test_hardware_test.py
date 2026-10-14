"""Runs the hardware test of spfi_encrypt against a software model of the system.

The model answers RMAP commands from a register model and turns Space Packets into authenticated chunks as the
Space Packet node, the framers and the AES-GCM cores do. It checks the test procedure and its use of the libraries
before a test on hardware; it is not a model of the RTL. Register addresses and fields come from the same headers as
the test (cores/<core>/sw/<prefix>_regs.h); the behaviour follows these documents of the pinned cores, so a change
of a core that the model does not follow shows up when the test runs on hardware or in the system simulation:

- RMAP target: replies of ECSS-E-ST-50-52C 5.3.2 and 5.4.2; commands of several words with incrementing addresses
  (5.3.1, 5.4.1); the key is checked after the reset and a wrong key gives the status "invalid key" (OpenRMAP,
  specification of omap_target TG-AU-04, register TGT_KEY.KEY_EN reset 1); data bytes in little-endian order
  (OpenRMAP user guide, endian order).
- SpaceFibre port: identification and the states of an initialised link and of active lanes (OpenFibre register map,
  DL_STATUS and LANE_STATUS; user guide, programming sequence).
- Space Packet node: every APID goes to receive port 0 after the reset (RxPathDefault_g, OpenSPP user guide,
  generics); the receive APID table is accessed through RX_TABLE_APID and RX_TABLE_ENTRY (user guide, programming
  sequence); the receive ports deliver the Packet Data Field and the send ports build the Packet Primary Header with
  the APID of their side band and a sequence count per APID (Octet String Service, user guide 4.1 and 4.2);
  destination 0 has the Target Logical Address 0xFE (reset value of DEST_CTRL); counters RX_PACKETS and TX_PACKETS.
- Framer and AES-GCM core: chunks of ChunkBytes_g bytes with their position (OpenCrypto, architecture of ocr_framer,
  section 2), key words with byte 4 n in bits 31:24, KEY_LOAD with START, IV_GEN and the slot, the load clears the
  key staging registers, IV_FIXED and IV_COUNT, the query of a slot through SLOT_QUERY and SLOT_STATUS to
  SLOT_COUNT_HI (user guide, programming sequence; register map), the dense output IV, position, ciphertext, tag
  (user guide 4.6), one invocation per chunk, counter ENCRYPTIONS.
- ECC monitor: INFO with the channel count and the counter width (Open Logic FT, olo_ft_ecc_monitor_axi).

By default the model is the system as its cores specify it. The model of the pinned cores adds the known defects of
the pinned commits (PINNED); the test of the procedure against it is an expected failure while one is pinned.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("hardware_test", HERE.parent / "hardware_test.py")
ht = importlib.util.module_from_spec(spec)
sys.modules["hardware_test"] = ht
spec.loader.exec_module(ht)

from refsys import chunks, rmap  # noqa: E402
from refsys.composer import SystemFile  # noqa: E402
from refsys.spp import SpacePacket, cptp_decode, cptp_encode  # noqa: E402
from refsys.transport import Transport, TransportError  # noqa: E402

SYSTEM = ht.System()
SYSTEM_FILE = SystemFile(ht.SYSTEM_DIR / "spfi_encrypt.composer.json")
PINS = {source["id"]: source["commit"] for source in SYSTEM_FILE.data["sources"]}

# Commits of OpenRMAP whose omap_core with 32-bit data and AxiMaxBeats_g = 1 gives every AXI transaction of an RMAP
# command the address of its first word: a read of several words repeats the first word, a write of several words
# writes all of them to the first register, where the last one stays, and leaves the other registers unchanged.
OMAP_FIRST_WORD_DEFECT = {
    "4ae4bfe31395ecbc31d5076796fd94bd7cb9de0e",
    "afb40c72afd5a7cc64e733c9ffcdfe78630d1125",
}
DEFECT_PINNED = (PINS["openrmap"] in OMAP_FIRST_WORD_DEFECT
                 and SYSTEM_FILE.generic(SYSTEM.rmap, "AxiDataWidth_g") == 32
                 and SYSTEM_FILE.generic(SYSTEM.rmap, "AxiMaxBeats_g") == 1)

SPECIFIED = (False, False)                    # reads and writes of several words as specified
PINNED = (True, True) if DEFECT_PINNED else SPECIFIED


class SystemModel(Transport):
    """spfi_encrypt as seen from the far end of its link.

    first_word: whether a read and whether a write of several words use the address of their first word for every
    word, as with the defect of OMAP_FIRST_WORD_DEFECT.
    """

    def __init__(self, setup: "ht.Setup", corrupt: bool = False, first_word: tuple[bool, bool] = SPECIFIED):
        s = SYSTEM
        self.setup = setup
        self.corrupt = corrupt
        self.first_word_read, self.first_word_write = first_word
        self.key = 0x00                                   # Key_g
        port, node = s.base(s.port), s.base(s.node)
        self.regs = {port + ht.OFB.ID: ht.OFB.REGMAP_ID, port + ht.OFB.DL_STATUS: ht.LINK_INITIALISED,
                     node + ht.SPP.ID: ht.SPP.REGMAP_ID, s.base(s.rmap) + ht.OMAP.ID: ht.OMAP.REGMAP_ID,
                     s.base(s.ecc) + ht.EDAC_INFO: s.ecc_info}
        for lane in range(4):
            self.regs[port + ht.OFB.LANE_STATUS(lane)] = ht.LANE_ACTIVE
        for ch in s.channels:
            core = s.base(ch.core)
            self.regs[core + ht.OCR.ID] = ht.OCR.REGMAP_ID
            self.regs[core + ht.OCR.GENERICS] = ht.OCR.field("GENERICS", "NUM_KEYS", ch.key_slots)
            self.regs[core + ht.OCR.RELEASE_BYTES] = SYSTEM_FILE.generic(ch.core, "ReleaseBytes_g")
        self.rx_table = {}
        self.slots = {}                                   # (core, slot): [key, fixed, next counter]
        self.seq = {}

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
        node = SYSTEM.base(SYSTEM.node)
        if address == node + ht.SPP.RX_TABLE_ENTRY:
            return self.rx_table.get(self.regs.get(node + ht.SPP.RX_TABLE_APID, 0), 0)
        return self.regs.get(address, 0)

    def _write(self, address, value):
        node = SYSTEM.base(SYSTEM.node)
        self.regs[address] = value
        if address == node + ht.SPP.RX_TABLE_ENTRY:
            self.rx_table[self.regs.get(node + ht.SPP.RX_TABLE_APID, 0)] = value
        for ch in SYSTEM.channels:
            core = SYSTEM.base(ch.core)
            if address == core + ht.OCR.KEY_LOAD:
                slot = ht.OCR.get("KEY_LOAD", "SLOT", value)
                assert ht.OCR.get("KEY_LOAD", "START", value) and ht.OCR.get("KEY_LOAD", "IV_GEN", value)
                assert slot < ch.key_slots
                staging = [core + ht.OCR.KEY(n) for n in range(8)] + [core + getattr(ht.OCR, r) for r in ht.OCR_IV]
                key = b"".join(self.regs.get(a, 0).to_bytes(4, "big") for a in staging[:8])
                counter = (self.regs.get(core + ht.OCR.IV_COUNT_HI, 0) << 32) | self.regs.get(
                    core + ht.OCR.IV_COUNT_LO, 0)
                self.slots[(core, slot)] = [key, self.regs.get(core + ht.OCR.IV_FIXED, 0), counter]
                for a in staging:
                    self.regs[a] = 0                      # the load clears the staging registers
            elif address == core + ht.OCR.SLOT_QUERY:
                state = [0, 0, 0, 0]
                if (core, value) in self.slots:
                    _, fixed, counter = self.slots[(core, value)]
                    state = ht.loaded_slot(fixed, counter)
                for r, v in zip(ht.OCR_SLOT, state):
                    self.regs[core + getattr(ht.OCR, r)] = v

    def _count(self, address, n=1):
        self.regs[address] = self.regs.get(address, 0) + n

    # --- Space Packets -----------------------------------------------------------------------------
    def _spp(self, packet):
        _, payload = cptp_decode(packet, self.setup.system_tla)
        sp = SpacePacket.decode(payload)
        node = SYSTEM.base(SYSTEM.node)
        entry = self.rx_table.get(sp.apid, ht.SPP.field("RX_TABLE_ENTRY", "ENABLE", 1))
        ch = SYSTEM.channels[ht.SPP.get("RX_TABLE_ENTRY", "PORT", entry)]
        self._count(node + ht.SPP.RX_PACKETS)
        slot = self.slots[(SYSTEM.base(ch.core), ch.slot)]
        key, fixed, counter = slot
        out = []
        for pos, part in chunks.split(sp.data, SYSTEM.chunk_bytes):
            data = bytearray(chunks.encrypt(key, fixed, counter, pos, part))
            if self.corrupt:
                data[20] ^= 1
            counter += 1
            slot[2] = counter
            seq = self.seq.get(ch.output_apid, 0)
            self.seq[ch.output_apid] = (seq + 1) & 0x3FFF
            out.append(cptp_encode(SpacePacket(apid=ch.output_apid, data=bytes(data), sequence_count=seq).encode(),
                                   self.setup.far_end_tla))
            self._count(SYSTEM.base(ch.core) + ht.OCR.ENCRYPTIONS)
        self._count(node + ht.SPP.TX_PACKETS, len(out))
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


def run_all(model, packets=24):
    ctx = ht.make_context(seed=3, packets=packets)
    regs = ht.Registers(model, ctx.setup)
    for test in ht.TESTS:
        test(regs, ctx)
    return ctx


def test_system_facts_from_the_composer_file():
    assert SYSTEM.windows == {"spp0": 0x00000, "ocr0": 0x10000, "edac0": 0x20000, "ocr1": 0x30000, "ocr2": 0x40000,
                              "ocr3": 0x50000, "rmap0": 0x60000, "ofb0": 0x70000}
    assert [(c.port, c.framer, c.core, c.output_apid, c.slot, c.key_slots) for c in SYSTEM.channels] == [
        (k, f"frm{k}", f"ocr{k}", k, 0, 4) for k in range(4)]
    assert (SYSTEM.chunk_bytes, SYSTEM.rx_buffer_bytes, SYSTEM.ecc_info) == (4096, 8192, 0x1008)


def test_procedure_passes_against_the_model():
    ctx = run_all(SystemModel(ht.Setup()))
    assert ctx.chunks >= 24


@pytest.mark.xfail(DEFECT_PINNED, raises=ht.TestFailed, strict=True,
                   reason="omap_core of the pinned OpenRMAP with AxiMaxBeats_g = 1 uses the address of the first "
                          "word for every word of a command")
def test_procedure_passes_against_the_pinned_cores():
    ctx = run_all(SystemModel(ht.Setup(), first_word=PINNED))
    assert ctx.chunks >= 24


def test_procedure_detects_a_read_of_several_words_at_the_first_address():
    with pytest.raises(ht.TestFailed, match="read of 16 bytes at ocr0.ID"):
        run_all(SystemModel(ht.Setup(), first_word=(True, False)))


def test_procedure_detects_a_write_of_several_words_to_the_first_register():
    with pytest.raises(ht.TestFailed, match="write of 12 bytes at ocr0.IV_FIXED"):
        run_all(SystemModel(ht.Setup(), first_word=(False, True)))


def test_procedure_detects_a_corrupted_chunk():
    with pytest.raises(ht.TestFailed, match="tag does not authenticate"):
        run_all(SystemModel(ht.Setup(), corrupt=True))


def test_procedure_detects_a_target_without_key_check():
    class AnyKey(SystemModel):
        def _rmap(self, packet):
            self.key = packet[len(self.setup.rmap_path) + 3]
            return super()._rmap(packet)
    with pytest.raises(ht.TestFailed, match="wrong key"):
        run_all(AnyKey(ht.Setup()))


def test_registers_of_one_command_are_consecutive():
    for names in (ht.OCR_INFO, ht.OCR_IV, ht.OCR_SLOT):
        ht.consecutive(ht.OCR, names)
    with pytest.raises(ValueError):
        ht.consecutive(ht.OCR, ("ID", "STATUS"))


def test_input_apids_must_not_overlap_the_output_apids():
    with pytest.raises(ValueError):
        ht.make_context(seed=1, packets=1, setup=ht.Setup(input_apid_base=2))
