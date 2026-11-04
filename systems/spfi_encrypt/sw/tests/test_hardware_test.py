"""Runs the hardware test of spfi_encrypt against the software model of the system (system_model.py).

By default the model is the system as its cores specify it. The model of the pinned versions adds their known defects
(PINNED); the test of the procedure against it is an expected failure while a version with a defect is pinned.
"""

import json
import random
import sys
from collections import deque
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import hardware_test as ht  # noqa: E402
from refsys import systest, transport  # noqa: E402
from refsys.composer import SystemFile  # noqa: E402
from refsys.transport import Transport, TransportError  # noqa: E402
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


def test_the_script_runs_the_pytest_tests_with_its_options(monkeypatch, tmp_path):
    """hardware_test.py --device/--model hands its options to pytest (systest.pytest_main)."""
    seen = []
    monkeypatch.setattr(pytest, "main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr(sys, "argv", ["hardware_test.py", "--device", "STAR-Ultra", "--seed", "3", "--profile", "sim",
                                      "--test", "TC-RS-HW-06", "--records", str(tmp_path)])
    assert ht.main() == 0
    suite = str(Path(ht.__file__).resolve().parent / "hwtest")
    assert seen == [[suite, "--target", "device", "--profile", "sim", "--seed", "3", "--results", str(tmp_path),
                     "--device", "STAR-Ultra", "--tc", "TC-RS-HW-06"]]
    monkeypatch.setattr(sys, "argv", ["hardware_test.py", "--model", "--records", str(tmp_path)])
    assert ht.main() == 0 and seen[-1][:3] == [suite, "--target", "model"] and "--device" not in seen[-1]


def run_all(model, packets=24):
    ctx = ht.make_context(seed=3, packets=packets)
    regs = ht.Registers(model, ctx.setup)
    ctx.selected = list(ht.TESTS)
    for test in ht.TESTS:
        ctx.current = systest.test_name(test)
        test(regs, ctx)
    return ctx


def test_system_facts_from_the_composer_file():
    assert SYSTEM.windows == {"spp0": 0x00000, "ocr0": 0x10000, "edac0": 0x20000, "ocr1": 0x30000, "ocr2": 0x40000,
                              "ocr3": 0x50000, "rmap0": 0x60000, "ofb0": 0x70000, "prst0": 0x80000}
    assert (SYSTEM.reset, SYSTEM.reset_key, SYSTEM.decoded_bytes()) == ("prst0", 0x50495045, 0x100000)
    assert SYSTEM.pipeline == ["spp0"] + [f"frm{k}" for k in range(4)] + [f"ocr{k}" for k in range(4)]
    assert [(c.port, c.framer, c.core, c.output_apid, c.slot, c.key_slots) for c in SYSTEM.channels] == [
        (k, f"frm{k}", f"ocr{k}", k, 0, 4) for k in range(4)]
    assert (SYSTEM.chunk_bytes, SYSTEM.rx_buffer_bytes, SYSTEM.ecc_info, SYSTEM.lanes) == (4096, 8192, 0x1008, 4)


def test_procedure_passes_against_the_model():
    ctx = run_all(SystemModel(ht.Setup()))
    assert ctx.chunks >= 24
    # the pipeline reset at the start of the run, the two commands of TC-RS-HW-19 and of TC-RS-HW-22 and the one of
    # TC-RS-HW-23; the second run of TC-RS-HW-18 skips the first and repeats the others
    texts = [r["text"] for r in ctx.recorded]
    assert texts.count("pipeline reset at the start of the run: prst0 STATUS.COUNT 1, BUSY 0 at poll 1") == 1
    assert "pipeline reset at the start of the run: skipped" in texts
    assert ctx.reset_count == 6


def test_the_reset_group_comes_from_the_composer_file(tmp_path):
    assert SYSTEM_FILE.reset_group("prst0.RstOut") == SYSTEM.pipeline
    with pytest.raises(KeyError, match="0 reset groups with the source edac0.RstOut"):
        SYSTEM_FILE.reset_group("edac0.RstOut")
    # a file of format version 1 has no reset group
    data = dict(SYSTEM_FILE.data)
    data.pop("resetGroups")
    path = tmp_path / "v1.composer.json"
    path.write_text(json.dumps(data | {"formatVersion": 1}), encoding="utf-8")
    with pytest.raises(KeyError, match="0 reset groups"):
        SystemFile(path).reset_group("prst0.RstOut")


def test_the_model_of_the_pipeline_reset_controller():
    m = SystemModel(ht.Setup())
    prst, node = SYSTEM.base("prst0"), SYSTEM.base("spp0")
    key = SYSTEM.reset_key
    # the register block repeats every 4 KiB of the 64 KiB window; another offset answers with an error
    assert m._bus_read(prst + systest.PRST_ID) == m._bus_read(prst + 0x3000) == systest.PRST_ID_VALUE
    assert m._bus_read(prst + systest.PRST_CTRL) == 0 and m._bus_read(prst + 0x0C) is None
    m.rx_table[0x30] = 1
    m.regs[node + systest.SPP.RX_PACKETS] = 5
    # writes that are no command: another value, a key without all byte enables, the key at STATUS
    for address, value, strobes in ((systest.PRST_CTRL, key ^ 1, 0xF), (systest.PRST_CTRL, key, 0x3),
                                     (systest.PRST_STATUS, key, 0xF)):
        assert m._bus_write(prst + address, value, strobes)
    assert m._bus_read(prst + systest.PRST_STATUS) == 0 and m.rx_table
    # a command: COUNT 1, the node at its reset values, the slots erased, the RMAP target untouched
    m.slots[(SYSTEM.base("ocr0"), 0)] = [bytes(32), 0, 0]
    window = SYSTEM.base("rmap0") + systest.OMAP.WIN_BASE(3)
    m.regs[window] = 0x1234
    assert m._bus_write(prst + 0x1000 + systest.PRST_CTRL, key)
    assert m._bus_read(prst + systest.PRST_STATUS) == 1 << systest.PRST_COUNT_SHIFT
    assert not m.rx_table and not m.slots and m.regs.get(node + systest.SPP.RX_PACKETS, 0) == 0
    assert m.regs[node + systest.SPP.CONFIG] == m.initial[node + systest.SPP.CONFIG] and m.regs[window] == 0x1234
    m.reset_count = systest.PRST_COUNT_MAX
    m._bus_write(prst + systest.PRST_CTRL, key)
    assert m._bus_read(prst + systest.PRST_STATUS) >> systest.PRST_COUNT_SHIFT == systest.PRST_COUNT_MAX


def test_a_partial_write_takes_the_bytes_of_its_byte_enables():
    m = SystemModel(ht.Setup())
    scratch = SYSTEM.base("rmap0") + systest.OMAP.WIN_BASE(3)
    m.regs[scratch] = 0x11223344
    assert m._bus_write(scratch, 0xAABB, 0x3) and m.regs[scratch] == 0x1122AABB


class ResetFault(SystemModel):
    """The system model with one fault of the pipeline reset (fault)."""

    def __init__(self, fault: str):
        super().__init__(ht.Setup())
        self.fault = fault

    def _pipeline_reset(self):
        slots = dict(self.slots)
        super()._pipeline_reset()
        if self.fault == "slots_kept":
            self.slots = slots

    def _write(self, address, value):
        super()._write(address, value)
        core = address - ht.OCR.SLOT_QUERY
        if (self.fault == "invocation" and core in [SYSTEM.base(c.core) for c in SYSTEM.channels]
                and self.regs.get(core + ht.OCR.ENCRYPTIONS, 0)):
            self.regs[core + ht.OCR.SLOT_COUNT_LO] += 1          # one invocation more than the chunks received


@pytest.mark.parametrize("fault, message", [
    ("slots_kept", "ocr0 slot 0 holds"),
    ("invocation", "a chunk is in flight or one was lost"),
])
def test_tc19_detects_a_faulty_pipeline_reset(fault, message):
    ctx = ht.make_context(seed=1, packets=ht.PROFILES["sim"].packets)
    results = dict(ht.run(ResetFault(fault), ctx, ht.select_tests(["TC-RS-HW-19"])))
    assert message in (results["TC-RS-HW-19"] or ""), results


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
            # TGT_KEY.KEY_EN 0: the target accepts every key
            self.regs[self.system.base(self.system.rmap) + systest.OMAP.TGT_KEY] = 0
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
    core_ids |= {c.core: ht.OCR.REGMAP_ID for c in SYSTEM.channels} | {SYSTEM.reset: systest.PRST_ID_VALUE}
    assert SYSTEM.ids == core_ids
    # channels of the ECC monitor: two memories per framer, in the order of the connections, as many as Channels_g
    assert SYSTEM.ecc_names == [f"FRM{k}_ECC_{m}" for k in range(4) for m in range(2)]
    assert SYSTEM.ecc_channels == SYSTEM_FILE.generic(SYSTEM.ecc, "Channels_g")
    channel = f"{SYSTEM.ecc.upper()}_CH_"
    values = sorted(v for n, v in ht.BANKS.constants().items() if n.startswith(channel))
    assert values == list(range(SYSTEM.ecc_channels))


class Channels(Transport):
    """The channels of the test equipment in front of the model: received packets wait in the order of their arrival
    and a packet that no request takes stays for the next one, as in the receive buffer of a channel.

    fault "chunks": the first exchange of several packets (TC-RS-HW-06) stops after half of them, as when its receive
    times out, and the other half stays on the channel. fault "reply": the reply to the first command arrives after its
    time-out and stays on the channel.
    """

    def __init__(self, fault: str | None = None):
        self.model = SystemModel(ht.Setup())
        self.waiting = {ht.Setup.rmap_channel: deque(), ht.Setup.spp_channel: deque()}
        self.fault = fault

    def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
        queue = self.waiting[rx_channel]
        queue.extend(self.model.exchange(tx_channel, packets, rx_channel, count, timeout))
        if (self.fault == "chunks" and count > 1) or (self.fault == "reply" and rx_channel == ht.Setup.rmap_channel):
            self.fault = None
            for _ in range(count // 2):
                queue.popleft()
            raise TransportError(f"receive on channel {rx_channel} did not complete within {timeout} s")
        return [queue.popleft() for _ in range(count)]

    def receive(self, channel, timeout):
        queue = self.waiting[channel]
        return queue.popleft() if queue else None


def lab_context(seed: int = 1):
    return ht.make_context(seed=seed, packets=ht.PROFILES["lab"].packets)


def aborted_run(fault: str) -> Channels:
    """The channels after a run that stopped early and left packets on a channel."""
    channels = Channels(fault)
    results = ht.run(channels, lab_context())
    assert results[-1][1] is not None and any(channels.waiting.values())
    return channels


def test_drain_reads_every_channel_until_it_is_empty(capsys):
    channels = Channels()
    channels.waiting[0].extend([b"\x01", b"\x02"])
    channels.waiting[1].extend([b"\x03", b"\x04", b"\x05"])
    assert ht.drain(channels, (0, 1)) == {0: 2, 1: 3}
    assert not any(channels.waiting.values())
    out = capsys.readouterr().out
    assert "channel 0: 2 packets of an earlier run discarded" in out
    assert "channel 1: 3 packets of an earlier run discarded" in out
    assert ht.drain(channels, (0, 1)) == {0: 0, 1: 0}


def test_drain_stops_at_a_channel_that_does_not_empty():
    class Endless(Transport):
        def receive(self, channel, timeout):
            return b"\x00"

    with pytest.raises(TransportError, match="channel 1 still delivers packets after 5 discarded ones"):
        ht.drain(Endless(), (1,), limit=5)


@pytest.mark.parametrize("fault", ["chunks", "reply"])
def test_a_rerun_without_the_drain_fails_on_the_packets_of_the_aborted_run(fault):
    channels = aborted_run(fault)
    results = ht.run(channels, lab_context(seed=2))
    assert any(error is not None for _, error in results)


def test_a_rerun_with_the_same_seed_without_the_drain_and_the_pipeline_reset_passes_tc06_on_the_aborted_chunks():
    # the aborted run sent the same chunks: TC-RS-HW-06 checks them instead of its own, which stay on the channel;
    # TC-RS-HW-19 then takes chunks of TC-RS-HW-06 for its own and finds their invocation counters behind the slot
    channels = aborted_run("chunks")
    ctx = lab_context()
    ctx.pipeline_reset = False
    results = ht.run(channels, ctx)
    assert results[5:7] == [("TC-RS-HW-06", None), ("TC-RS-HW-07", None)]
    assert results[-1][0] == "TC-RS-HW-19" and "first counter" in results[-1][1]


def test_a_rerun_with_the_same_seed_without_the_drain_fails_after_the_pipeline_reset():
    # the pipeline reset starts the Packet Sequence Counts again: the chunks of the aborted run do not continue them
    channels = aborted_run("chunks")
    results = ht.run(channels, lab_context())
    assert results[-1] == ("TC-RS-HW-06", "APID 0: Packet Sequence Counts [7, 8, 9, 10, 11, 12, 13, 14]")


@pytest.mark.parametrize("fault", ["chunks", "reply"])
def test_the_script_drains_the_channels_before_the_first_test_case(fault, monkeypatch, capsys):
    channels = aborted_run(fault)
    left = {channel: len(queue) for channel, queue in channels.waiting.items()}
    capsys.readouterr()
    monkeypatch.setattr(transport, "StarSystemTransport", lambda name: channels)
    ctx = ht.make_context(seed=1, packets=ht.PROFILES["lab"].packets)
    device = systest.open_device("STAR-Ultra", ctx)
    results = ht.run(device, ctx)
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == [f"channel {channel}: {count} packets of an earlier run discarded"
                       for channel, count in left.items()]
    assert results == [(ht.test_name(t), None) for t in ht.TESTS]
    # every request of the run got its own reply: nothing is left on the channels
    assert not any(channels.waiting.values())
