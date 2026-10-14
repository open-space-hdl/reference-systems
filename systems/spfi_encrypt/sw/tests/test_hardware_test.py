"""Runs the hardware test of spfi_encrypt against a software model of the system.

The model answers RMAP commands from a register model and turns Space Packets into authenticated chunks as the
Space Packet node, the framers and the AES-GCM cores do. It checks the test procedure and its use of the libraries
before a test on hardware; it is not a model of the RTL. Register addresses and fields come from the same headers as
the test (cores/<core>/sw/<prefix>_regs.h); the behaviour follows these documents of the pinned cores, so a change
of a core that the model does not follow shows up when the test runs on hardware or in the system simulation:

- RMAP target: replies of ECSS-E-ST-50-52C 5.3.2 and 5.4.2; the key is checked after the reset and a wrong key
  gives the status "invalid key" (OpenRMAP, specification of omap_target TG-AU-04, register TGT_KEY.KEY_EN reset 1);
  data bytes in little-endian order (OpenRMAP user guide, endian order).
- SpaceFibre port: identification and the states of an initialised link and of active lanes (OpenFibre register map,
  DL_STATUS and LANE_STATUS; user guide, programming sequence).
- Space Packet node: every APID goes to receive port 0 after the reset (RxPathDefault_g, OpenSPP user guide,
  generics); the receive APID table is accessed through RX_TABLE_APID and RX_TABLE_ENTRY (user guide, programming
  sequence); the receive ports deliver the Packet Data Field and the send ports build the Packet Primary Header with
  the APID of their side band and a sequence count per APID (Octet String Service, user guide 4.1 and 4.2);
  destination 0 has the Target Logical Address 0xFE (reset value of DEST_CTRL); counters RX_PACKETS and TX_PACKETS.
- Framer and AES-GCM core: chunks of ChunkBytes_g bytes with their position (OpenCrypto, architecture of ocr_framer,
  section 2), key words with byte 4 n in bits 31:24, KEY_LOAD with START, IV_GEN and the slot (user guide,
  programming sequence), the dense output IV, position, ciphertext, tag (user guide 4.6), one invocation per chunk,
  counter ENCRYPTIONS.
- ECC monitor: INFO with the channel count and the counter width (Open Logic FT, olo_ft_ecc_monitor_axi).
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
from refsys.spp import SpacePacket, cptp_decode, cptp_encode  # noqa: E402
from refsys.transport import Transport, TransportError  # noqa: E402

SYSTEM = ht.System()


class SystemModel(Transport):
    """spfi_encrypt as seen from the far end of its link."""

    def __init__(self, setup: "ht.Setup", corrupt: bool = False):
        s = SYSTEM
        self.setup = setup
        self.corrupt = corrupt
        self.key = 0x00                                   # Key_g
        port, node = s.base(s.port), s.base(s.node)
        self.regs = {port + ht.OFB.ID: ht.OFB.REGMAP_ID, port + ht.OFB.DL_STATUS: ht.LINK_INITIALISED,
                     node + ht.SPP.ID: ht.SPP.REGMAP_ID, s.base(s.rmap) + ht.OMAP.ID: ht.OMAP.REGMAP_ID,
                     s.base(s.ecc) + ht.EDAC_INFO: s.ecc_info}
        for lane in range(4):
            self.regs[port + ht.OFB.LANE_STATUS(lane)] = ht.LANE_ACTIVE
        for ch in s.channels:
            self.regs[s.base(ch.core) + ht.OCR.ID] = ht.OCR.REGMAP_ID
        self.rx_table = {}
        self.slots = {}
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
        status = 0 if key == self.key else 3
        data = b""
        if write and status == 0:
            assert rmap.crc8(cmd[len(header):]) == 0
            self._write(address, int.from_bytes(cmd[len(header):len(header) + length], "little"))
        elif status == 0:
            data = self._read(address).to_bytes(4, "little")[:length]
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
                assert ht.OCR.get("KEY_LOAD", "SLOT", value) == ch.slot and ht.OCR.get("KEY_LOAD", "IV_GEN", value)
                key = b"".join(self.regs.get(core + ht.OCR.KEY(n), 0).to_bytes(4, "big") for n in range(8))
                counter = (self.regs[core + ht.OCR.IV_COUNT_HI] << 32) | self.regs[core + ht.OCR.IV_COUNT_LO]
                self.slots[ch.port] = [key, self.regs[core + ht.OCR.IV_FIXED], counter]

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
        key, fixed, counter = self.slots[ch.port]
        out = []
        for pos, part in chunks.split(sp.data, SYSTEM.chunk_bytes):
            data = bytearray(chunks.encrypt(key, fixed, counter, pos, part))
            if self.corrupt:
                data[20] ^= 1
            counter += 1
            self.slots[ch.port][2] = counter
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
    assert [(c.port, c.framer, c.core, c.output_apid, c.slot) for c in SYSTEM.channels] == [
        (k, f"frm{k}", f"ocr{k}", k, 0) for k in range(4)]
    assert (SYSTEM.chunk_bytes, SYSTEM.rx_buffer_bytes, SYSTEM.ecc_info) == (4096, 8192, 0x1008)


def test_procedure_passes_against_the_model():
    ctx = run_all(SystemModel(ht.Setup()))
    assert ctx.chunks >= 24


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


def test_input_apids_must_not_overlap_the_output_apids():
    with pytest.raises(ValueError):
        ht.make_context(seed=1, packets=1, setup=ht.Setup(input_apid_base=2))
