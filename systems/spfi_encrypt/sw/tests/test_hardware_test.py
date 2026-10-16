"""Runs the hardware test of spfi_encrypt against the software model of the system (system_model.py).

By default the model is the system as its cores specify it. The model of the pinned versions adds their known defects
(PINNED); the test of the procedure against it is an expected failure while a version with a defect is pinned.
"""

import random
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import hardware_test as ht  # noqa: E402
from system_model import SPECIFIED, SYSTEM, SYSTEM_FILE, SystemModel  # noqa: E402

OLO_TAG = next(source["tag"] for source in SYSTEM_FILE.data["sources"] if source["id"] == "olo")


def ft_version(tag: str) -> tuple[int, ...]:
    base, _, ft = tag.partition("-ft.")
    return tuple(int(part) for part in base.split(".")) + (int(ft),)


# Open Logic FT up to this tag: olo_axi_master_simple gives every AXI transaction of a command that it splits the start
# address of the command when AxiMaxBeats_g < AxiDataWidth_g / 8. The model reproduces the case of rmap0, omap_core
# with 32-bit data and AxiMaxBeats_g = 1: a read of several words repeats the first word, a write of several words
# writes all of them to the first register, where the last one stays, and leaves the other registers unchanged.
OLO_FIRST_WORD_DEFECT_UNTIL = "4.7.0-ft.4"
DEFECT_PINNED = (ft_version(OLO_TAG) <= ft_version(OLO_FIRST_WORD_DEFECT_UNTIL)
                 and SYSTEM_FILE.generic(SYSTEM.rmap, "AxiDataWidth_g") == 32
                 and SYSTEM_FILE.generic(SYSTEM.rmap, "AxiMaxBeats_g") == 1)

PINNED = (True, True) if DEFECT_PINNED else SPECIFIED


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
    assert (SYSTEM.chunk_bytes, SYSTEM.rx_buffer_bytes, SYSTEM.ecc_info, SYSTEM.lanes) == (4096, 8192, 0x1008, 4)


def test_procedure_passes_against_the_model():
    ctx = run_all(SystemModel(ht.Setup()))
    assert ctx.chunks >= 24


@pytest.mark.xfail(DEFECT_PINNED, raises=ht.TestFailed, strict=True,
                   reason="olo_axi_master_simple of the pinned Open Logic FT gives every word of a command of rmap0 "
                          "the address of the first word")
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


def test_packet_lengths_start_with_the_boundary_cases():
    n, longest = SYSTEM.chunk_bytes, SYSTEM.rx_buffer_bytes - 6
    for profile in ht.PROFILES.values():
        lengths = ht.packet_lengths(SYSTEM, profile.packets, random.Random(1))
        assert lengths[:5] == [1, n - 1, n, n + 1, longest] and len(lengths) == profile.packets
        assert all(1 <= x <= longest for x in lengths)
    with pytest.raises(ValueError):
        ht.packet_lengths(SYSTEM, 4, random.Random(1))


def test_a_selected_test_case_brings_those_it_builds_on():
    names = [ht.test_name(t) for t in ht.select_tests(["TC-RS-HW-07", "TC-RS-HW-02"])]
    assert names == ["TC-RS-HW-02", "TC-RS-HW-05", "TC-RS-HW-06", "TC-RS-HW-07"]
    assert ht.select_tests(None) == ht.TESTS
    with pytest.raises(ValueError):
        ht.select_tests(["TC-RS-HW-99"])


def test_register_banks_of_the_exported_header_match_the_system():
    # bases: the windows of the AXI4-Lite decoder of the Composer file
    assert SYSTEM.windows == SYSTEM_FILE.decoder_windows(SYSTEM_FILE.instance_of_block("olo/olo_axi_lite_decoder"))
    # identifications: the register map identifications of the pinned cores
    core_ids = {SYSTEM.node: ht.SPP.REGMAP_ID, SYSTEM.rmap: ht.OMAP.REGMAP_ID, SYSTEM.port: ht.OFB.REGMAP_ID}
    core_ids |= {c.core: ht.OCR.REGMAP_ID for c in SYSTEM.channels}
    assert SYSTEM.ids == core_ids
    # channels of the ECC monitor: two memories per framer, in the order of the connections, as many as Channels_g
    assert SYSTEM.ecc_names == [f"FRM{k}_ECC_{m}" for k in range(4) for m in range(2)]
    assert SYSTEM.ecc_channels == SYSTEM_FILE.generic(SYSTEM.ecc, "Channels_g")
    channel = f"{SYSTEM.ecc.upper()}_CH_"
    values = sorted(v for n, v in ht.BANKS.constants().items() if n.startswith(channel))
    assert values == list(range(SYSTEM.ecc_channels))
