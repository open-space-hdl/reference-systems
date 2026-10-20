"""Checks the stimuli and the checks of the system simulation of spfi_compress (vectors.py) without a simulator.

A Python stand-in for the bench (refsys.sysvectors.play) plays stimulus.txt against the software model of the system
at the level of the virtual channels of the far end and writes the records in the format of the bench. The system is
that of the Composer file with smaller images and chunks (hardware_test.System dims and chunk_bytes), so that the
model compresses them in seconds and every compressed image takes several Space Packets.
"""

from pathlib import Path

from refsys import systest, sysvectors

HERE = Path(__file__).resolve().parent
(vectors,) = systest.system_modules(HERE.parent, "vectors")
ht, sm = vectors.ht, vectors.sm

SETUP = ht.Setup()
SYSTEM = ht.System(dims=(16, 8, 6), chunk_bytes=256)      # 768 samples, 1536 bytes per image
BENCH = vectors.make_bench(SYSTEM, sdu_bytes=200)


def play(directory: Path, model=None, **kw) -> None:
    sysvectors.play(directory, model or sm.SystemModel(SETUP, system=SYSTEM), SYSTEM, SETUP, **kw)


def results(report: str) -> dict[str, str]:
    return {line.split()[0]: line.split()[1] for line in report.splitlines() if line.startswith("TC-RS-HW-")}


def operations(directory: Path) -> list[list[str]]:
    return [t.split() for t in (directory / sysvectors.STIMULUS).read_text(encoding="utf-8").splitlines()
            if t and not t.startswith("#")]


def test_records_of_the_model_pass(tmp_path):
    sysvectors.write(BENCH, tmp_path, "sim", 1, None)
    play(tmp_path)
    assert sysvectors.check(BENCH, tmp_path) == 0
    report = (tmp_path / sysvectors.REPORT).read_text(encoding="utf-8")
    assert results(report) == {ht.test_name(t): "PASS" for t in ht.TESTS}
    assert "1 image per channel in TC-RS-HW-06" in report


def test_the_procedure_knows_every_space_packet_in_advance(tmp_path):
    # the model of OpenCompact gives every compressed image, the aborted ones included: the procedure never receives
    # until a channel is quiet
    sysvectors.write(BENCH, tmp_path, "sim", 1, None)
    assert not [op for op in operations(tmp_path) if op[0] == "quiet"]


def append_last_packet(directory: Path) -> None:
    """The system sends the last Space Packet that the far end received once more."""
    path = directory / sysvectors.rx_file(SYSTEM.spp_vc)
    rx = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(rx + rx[-1:]) + "\n", encoding="utf-8")


def test_a_space_packet_that_no_request_awaits_fails(tmp_path):
    sysvectors.write(BENCH, tmp_path, "sim", 1, ["TC-RS-HW-08"])
    play(tmp_path)
    append_last_packet(tmp_path)
    assert sysvectors.check(BENCH, tmp_path) == 1
    assert "packets that no request awaited" in (tmp_path / sysvectors.REPORT).read_text(encoding="utf-8")


def test_a_lost_space_packet_fails(tmp_path):
    sysvectors.write(BENCH, tmp_path, "sim", 1, ["TC-RS-HW-06"])
    play(tmp_path, drop_last=True)
    assert sysvectors.check(BENCH, tmp_path) == 1
    assert results((tmp_path / sysvectors.REPORT).read_text(encoding="utf-8"))["TC-RS-HW-06"] == "FAIL"


def test_the_bench_of_the_composer_file_has_the_lab_images():
    ctx = vectors.BENCH.context(1, "sim")
    assert {c.cfg["large_n_x"] * c.cfg["large_n_y"] * c.cfg["large_n_z"] for c in ctx.system.channels} == {
        64 * 64 * 48}
    assert {c.chunk_bytes for c in ctx.system.channels} == {4096}
