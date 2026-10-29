"""Checks the stimuli and the checks of the system simulation (vectors.py) without a simulator.

A Python stand-in for the bench plays stimulus.txt against the software model of the system at the level of the
virtual channels of the far end and writes the records in the format of the bench.
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import vectors  # noqa: E402
from refsys import sysvectors  # noqa: E402
from vectors import ht  # noqa: E402
from system_model import SystemModel  # noqa: E402

SETUP = ht.Setup()
SYSTEM = ht.System()


def play(directory: Path, model=None, drop_last: bool = False, reply_address: bytes | None = None) -> None:
    """Plays stimulus.txt as the bench does and writes events.txt and rx_vc<n>.txt."""
    sysvectors.play(directory, model or SystemModel(SETUP), SYSTEM, SETUP, drop_last, reply_address)


REQUEST_NS = vectors.REQUEST_TIMEOUT_NS


def results(report: str) -> dict[str, str]:
    return {line.split()[0]: line.split()[1] for line in report.splitlines() if line.startswith("TC-RS-HW-")}


def test_records_of_the_model_pass(tmp_path):
    vectors.write(tmp_path, "sim", 1, None)
    play(tmp_path)
    assert vectors.check(tmp_path) == 0
    report = (tmp_path / vectors.REPORT).read_text(encoding="utf-8")
    assert results(report) == {ht.test_name(t): "PASS" for t in ht.TESTS}
    assert "link initialised" in report


def test_report_lists_the_receiver_reset_records_of_every_lane(tmp_path):
    vectors.write(tmp_path, "sim", 1, ["TC-RS-HW-01"])
    play(tmp_path)
    assert vectors.check(tmp_path) == 0
    report = (tmp_path / vectors.REPORT).read_text(encoding="utf-8").splitlines()
    records = report[report.index("Recorded values (no pass criteria)") + 1:]
    assert [r.split(":")[0].strip() for r in records] == [f"{SYSTEM.port} lane {n}" for n in range(SYSTEM.lanes)]
    assert all("RX_RESETS 0, initialisation timeouts 0" in r for r in records)


def test_stimulus_waits_for_the_link_and_marks_every_test_case(tmp_path):
    vectors.write(tmp_path, "sim", 1, None)
    ops = [t.split() for t in (tmp_path / vectors.STIMULUS).read_text(encoding="utf-8").splitlines()
           if t and not t.startswith("#")]
    assert ops[0][0] == "mibp" and int(ops[0][1], 16) == ht.OFB.DL_STATUS and ops[-1] == ["end"]
    # the system starts the link: the far end writes no register of its MIB
    assert not any(op[0] == "mibw" for op in ops)
    assert [op[1] for op in ops if op[0] == "mark"] == [ht.test_name(t) for t in ht.TESTS]
    # every command on the RMAP channel left without the path address of the routing switch
    assert all(not op[3].startswith(SETUP.rmap_path.hex()) for op in ops if op[0] == "send" and op[1] == "0")
    assert sum(op[0] == "poll" for op in ops) > 0


def test_sim_profile_keeps_the_boundary_cases(tmp_path):
    vectors.write(tmp_path, "sim", 1, None)
    data = json.loads((tmp_path / vectors.VECTORS).read_text(encoding="utf-8"))
    assert data["packets"] == ht.PROFILES["sim"].packets
    n = SYSTEM.chunk_bytes
    import random
    assert ht.packet_lengths(SYSTEM, data["packets"], random.Random(1))[:5] == [1, n - 1, n, n + 1, 8186]


def test_a_selected_test_case_runs_with_those_it_builds_on(tmp_path):
    vectors.write(tmp_path, "sim", 1, ["TC-RS-HW-07"])
    play(tmp_path)
    assert vectors.check(tmp_path) == 0
    report = (tmp_path / vectors.REPORT).read_text(encoding="utf-8")
    assert results(report) == {"TC-RS-HW-05": "PASS", "TC-RS-HW-06": "PASS", "TC-RS-HW-07": "PASS"}


def test_a_corrupted_chunk_fails(tmp_path):
    vectors.write(tmp_path, "sim", 1, None)
    play(tmp_path, SystemModel(SETUP, corrupt=True))
    assert vectors.check(tmp_path) == 1
    assert results((tmp_path / vectors.REPORT).read_text(encoding="utf-8"))["TC-RS-HW-06"] == "FAIL"


def test_a_missing_packet_fails(tmp_path):
    vectors.write(tmp_path, "sim", 1, None)
    play(tmp_path, drop_last=True)
    assert vectors.check(tmp_path) == 1
    report = (tmp_path / vectors.REPORT).read_text(encoding="utf-8")
    assert results(report)["TC-RS-HW-06"] == "FAIL"
    assert "within the simulated time limit" in report


def test_a_wrong_reply_address_fails(tmp_path):
    vectors.write(tmp_path, "sim", 1, None)
    play(tmp_path, reply_address=b"\x02")
    assert vectors.check(tmp_path) == 1
    report = (tmp_path / vectors.REPORT).read_text(encoding="utf-8")
    assert results(report)["TC-RS-HW-01"] == "FAIL" and "reply address" in report


def test_a_request_that_differs_from_the_recording_fails(tmp_path):
    vectors.write(tmp_path, "sim", 1, None)
    play(tmp_path)
    data = json.loads((tmp_path / vectors.VECTORS).read_text(encoding="utf-8"))
    op = next(op for op in data["ops"] if op["kind"] == "exchange")
    op["packets"][0] = op["packets"][0][:-2] + "00"
    (tmp_path / vectors.VECTORS).write_text(json.dumps(data), encoding="utf-8")
    assert vectors.check(tmp_path) == 1
    assert "differs from the recorded one" in (tmp_path / vectors.REPORT).read_text(encoding="utf-8")


def test_an_unfinished_simulation_fails(tmp_path):
    vectors.write(tmp_path, "sim", 1, None)
    play(tmp_path)
    events = (tmp_path / vectors.EVENTS).read_text(encoding="utf-8").splitlines()
    (tmp_path / vectors.EVENTS).write_text("\n".join(e for e in events if " end " not in e), encoding="utf-8")
    assert vectors.check(tmp_path) == 1
    assert "did not reach the end" in (tmp_path / vectors.REPORT).read_text(encoding="utf-8")


def test_unknown_test_case_is_refused(tmp_path):
    with pytest.raises(ValueError):
        vectors.write(tmp_path, "sim", 1, ["TC-RS-HW-99"])


def test_the_check_writes_the_step_log_and_can_run_again_on_earlier_records(tmp_path):
    vectors.write(tmp_path, "sim", 1, None)
    play(tmp_path)
    assert vectors.check(tmp_path) == 0
    log = [json.loads(line) for line in (tmp_path / sysvectors.STEPS).read_text(encoding="utf-8").splitlines()]
    assert {e["kind"] for e in log} >= {"step", "read", "expect", "packets"}
    assert "TC-RS-HW-06" in (tmp_path / sysvectors.STEPS_TEXT).read_text(encoding="utf-8")
    # the records of that simulation, checked again into another directory: the report there, the records untouched
    again = tmp_path / "again"
    report = (tmp_path / sysvectors.REPORT).read_text(encoding="utf-8")
    assert vectors.check(tmp_path, again) == 0
    assert results((again / sysvectors.REPORT).read_text(encoding="utf-8")) == results(report)
    assert (again / sysvectors.STEPS).exists() and (tmp_path / sysvectors.REPORT).read_text(encoding="utf-8") == report


def bench_runner():
    sys.path.insert(0, str(HERE.parents[3] / "tools"))
    import sysbench  # noqa: PLC0415 - tools/sysbench.py, the runner of the system simulations

    return sysbench.recheck, sysbench.Simulation("spfi_encrypt", vectors)


def test_recheck_records_the_commit_and_the_result(tmp_path):
    recheck, sim = bench_runner()
    vectors.write(tmp_path, "sim", 1, None)
    play(tmp_path)
    assert recheck(sim, tmp_path) == 0
    out = next(tmp_path.glob("recheck_*"))
    lines = (out / "recheck.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"records: {tmp_path}" and lines[1].startswith("code: ") and lines[3] == "result: passed"
    assert (out / sysvectors.REPORT).exists() and (out / sysvectors.STEPS).exists()


def test_recheck_fails_loudly_when_a_request_differs_from_the_recorded_one(tmp_path, capsys):
    recheck, sim = bench_runner()
    vectors.write(tmp_path, "sim", 1, None)
    play(tmp_path)
    # the records of a procedure that sent other words in TC-RS-HW-03: the requests of this code are not the recorded
    data = json.loads((tmp_path / vectors.VECTORS).read_text(encoding="utf-8"))
    exchange = next(op for op in data["ops"] if op["kind"] == "exchange" and op["packets"])
    exchange["packets"][0] = exchange["packets"][0][:-2] + ("00" if exchange["packets"][0][-2:] != "00" else "01")
    (tmp_path / vectors.VECTORS).write_text(json.dumps(data), encoding="utf-8")
    assert recheck(sim, tmp_path) == 2
    assert "REQUEST MISMATCH" in capsys.readouterr().out
    assert "result: REQUEST MISMATCH" in (next(tmp_path.glob("recheck_*")) / "recheck.txt").read_text(encoding="utf-8")
