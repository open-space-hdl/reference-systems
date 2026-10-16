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
from vectors import ht  # noqa: E402
from system_model import SystemModel  # noqa: E402

SETUP = ht.Setup()
SYSTEM = ht.System()


def play(directory: Path, model=None, drop_last: bool = False, reply_address: bytes | None = None) -> None:
    """Plays stimulus.txt as the bench does and writes events.txt and rx_vc<n>.txt."""
    model = model or SystemModel(SETUP)
    by_vc = {r.vc: (ch, r) for ch, r in vectors.routes(SYSTEM, SETUP).items()}
    events = ["900 ready -1"]
    rx = {vc: [] for vc in by_vc}
    pending = {vc: [] for vc in by_vc}
    ns, index = 1000, -1
    for text in (directory / vectors.STIMULUS).read_text(encoding="utf-8").splitlines():
        if not text or text.startswith("#"):
            continue
        index += 1
        ns += 100
        f = text.split()
        if f[0] == "mark":
            events.append(f"{ns} mark {index} {f[1]}")
        elif f[0] == "mibp":
            events.append(f"{ns} mibp {index} 3")
        elif f[0] == "send":
            assert len(bytes.fromhex(f[3])) == int(f[2])
            pending[int(f[1])].append(bytes.fromhex(f[3]))
        elif f[0] in ("recv", "poll"):
            vc = int(f[1])
            channel, route = by_vc[vc]
            if f[0] == "poll":
                packets, count = [bytes.fromhex(f[7])], 1
            else:
                packets, count = pending[vc], int(f[2])
                pending[vc] = []
            replies = model.exchange(channel, [route.tx_path + p for p in packets], channel, count)
            for reply in replies:
                data = (route.rx_path if reply_address is None else reply_address) + reply
                rx[vc].append(f"{ns} {len(data)} 0 {data.hex()}")
            if f[0] == "poll":
                events.append(f"{ns} poll {index} 1")
            elif drop_last and count > 1:
                rx[vc].pop()
                events.append(f"{ns + REQUEST_NS} timeout {index}")
                break
        elif f[0] == "end":
            events.append(f"{ns} end {index}")
        else:
            assert f[0] == "mibw" and len(f) == 3
    (directory / vectors.EVENTS).write_text("\n".join(events) + "\n", encoding="utf-8")
    for vc, lines in rx.items():
        (directory / vectors.rx_file(vc)).write_text("".join(f"{t}\n" for t in lines), encoding="utf-8")


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
