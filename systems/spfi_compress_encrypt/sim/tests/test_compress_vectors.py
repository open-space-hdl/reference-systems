"""Checks the stimuli and the checks of the system simulation of spfi_compress_encrypt (vectors.py) without a
simulator.

A Python stand-in for the bench (refsys.sysvectors.play) plays stimulus.txt against the software model of the system
at the level of the virtual channels of the far end and writes the records in the format of the bench. The system is
that of the Composer file with smaller images (hardware_test.System dims), so that the model compresses them in
seconds.
"""

import json
from pathlib import Path

import pytest

from refsys import ocp, systest, sysvectors

HERE = Path(__file__).resolve().parent
(vectors,) = systest.system_modules(HERE.parent, "vectors")
ht, sm = vectors.ht, vectors.sm

SETUP = ht.Setup()
SYSTEM = ht.System(dims=(16, 8, 6))           # 768 samples, 1536 bytes per image
BENCH = vectors.make_bench(SYSTEM, sdu_bytes=200)
# OpenCompact gives the compressed image of an aborted image: the procedure then knows every chunk in advance
EXACT_ABORT = hasattr(ocp.ocp_model, "compress_aborted")
QUIET = pytest.mark.skipif(EXACT_ABORT, reason="with exact aborted images the procedure never waits for quiet")


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


def test_only_the_images_with_aborted_ones_are_received_until_the_channel_is_quiet(tmp_path):
    sysvectors.write(BENCH, tmp_path, "sim", 1, None)
    ops = operations(tmp_path)
    marks = [i for i, op in enumerate(ops) if op[0] == "mark"]
    tc08 = next(i for i in marks if ops[i][1] == "TC-RS-HW-08")
    quiet = [i for i, op in enumerate(ops) if op[0] == "quiet"]
    if EXACT_ABORT:
        assert quiet == []
        return
    # the images of TC-RS-HW-08 have aborted ones, whose length the model does not give: after the chunks it knows,
    # the procedure receives on the channel of the Space Packets until it is quiet
    assert len(quiet) == 1 and tc08 < quiet[0] < next(m for m in marks if m > tc08)
    assert ops[quiet[0]][1:] == [str(SYSTEM.spp_vc), str(sysvectors.QUIET_NS), str(sysvectors.QUIET_LIMIT_NS)]
    assert ops[quiet[0] - 1][0] == "recv"


def append_last_chunk(directory: Path) -> None:
    """The system sends the last chunk that the far end received once more."""
    path = directory / sysvectors.rx_file(SYSTEM.spp_vc)
    rx = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(rx + rx[-1:]) + "\n", encoding="utf-8")


@QUIET
def test_a_chunk_too_many_in_the_quiet_window_fails(tmp_path):
    sysvectors.write(BENCH, tmp_path, "sim", 1, ["TC-RS-HW-08"])
    play(tmp_path)
    data = json.loads((tmp_path / sysvectors.VECTORS).read_text(encoding="utf-8"))
    line = next(op["line"] for op in data["ops"] if op["kind"] == "quiet")
    append_last_chunk(tmp_path)
    events = []
    for e in (tmp_path / sysvectors.EVENTS).read_text(encoding="utf-8").splitlines():
        f = e.split()
        if f[1] == "quiet" and int(f[2]) == line:
            f[3] = str(int(f[3]) + 1)
        events.append(" ".join(f))
    (tmp_path / sysvectors.EVENTS).write_text("\n".join(events) + "\n", encoding="utf-8")
    assert sysvectors.check(BENCH, tmp_path) == 1
    assert results((tmp_path / sysvectors.REPORT).read_text(encoding="utf-8"))["TC-RS-HW-08"] == "FAIL"


def test_a_chunk_that_no_request_awaits_fails(tmp_path):
    sysvectors.write(BENCH, tmp_path, "sim", 1, ["TC-RS-HW-08"])
    play(tmp_path)
    append_last_chunk(tmp_path)
    assert sysvectors.check(BENCH, tmp_path) == 1
    assert "packets that no request awaited" in (tmp_path / sysvectors.REPORT).read_text(encoding="utf-8")


@QUIET
def test_a_channel_that_does_not_become_quiet_fails(tmp_path):
    sysvectors.write(BENCH, tmp_path, "sim", 1, ["TC-RS-HW-08"])
    play(tmp_path)
    events = [e for e in (tmp_path / sysvectors.EVENTS).read_text(encoding="utf-8").splitlines()
              if e.split()[1] != "quiet"]
    (tmp_path / sysvectors.EVENTS).write_text("\n".join(events) + "\n", encoding="utf-8")
    assert sysvectors.check(BENCH, tmp_path) == 1
    assert "was not quiet within" in (tmp_path / sysvectors.REPORT).read_text(encoding="utf-8")


def test_the_bench_of_the_composer_file_has_the_lab_images():
    ctx = vectors.BENCH.context(1, "sim")
    assert {c.cfg["large_n_x"] * c.cfg["large_n_y"] * c.cfg["large_n_z"] for c in ctx.system.channels} == {
        64 * 64 * 48}


def test_the_limit_of_an_exchange_includes_the_work_of_the_compressors(tmp_path, monkeypatch):
    # an image in BI order with a small runtime M takes the predictor several cycles per sample (TC-RS-HW-10 sends
    # channel 0 such an image): the procedure tells the bench the cycles of the images of an exchange
    cycles = 10 ** 6
    monkeypatch.setattr(vectors.ht.ocp, "image_cycles", lambda cfg: cycles)
    sysvectors.write(BENCH, tmp_path, "sim", 1, ["TC-RS-HW-10"])
    limits = [int(op[3]) for op in operations(tmp_path) if op[0] == "recv" and int(op[2]) > 1]
    busy = cycles * sysvectors.BUSY_NS_PER_CYCLE * 5 // 4
    assert limits and all(limit > busy for limit in limits)
    monkeypatch.undo()
    sysvectors.write(BENCH, tmp_path, "sim", 1, ["TC-RS-HW-10"])
    assert all(int(op[3]) < busy for op in operations(tmp_path) if op[0] == "recv")
