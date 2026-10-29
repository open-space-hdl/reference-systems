# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of the pytest framework of the hardware tests (sw/refsys/hwplugin.py, sw/refsys/steplog.py): pytest runs in
a subprocess against the system model of spfi_encrypt (the smallest system, sim profile), as the lab runs it, and
the tests check its outputs, its results and that it leaves the repository as it found it."""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from refsys import hwplugin, steplog

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "systems" / "spfi_encrypt" / "sw" / "hwtest"


def session(results: Path, *options: str, bytecode: bool = False,
            env: dict | None = None) -> subprocess.CompletedProcess:
    """python -B -m pytest of the hardware tests of spfi_encrypt against its model, sim profile; env: variables that
    replace those of this process."""
    env = dict(os.environ) | (env or {})
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    # -p plugins are imported before the pythonpath of pytest.ini applies: the fault plugin and refsys from the path
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "sw" / "tests"), str(ROOT / "sw")])
    command = [sys.executable] + ([] if bytecode else ["-B"]) + ["-m", "pytest", str(SUITE), "--target", "model",
                                                                    "--profile", "sim", "--results", str(results),
                                                                    *options]
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env, timeout=600, check=False)


def run_dir(results: Path) -> Path:
    runs = sorted(p for p in results.iterdir() if p.is_dir())
    assert len(runs) == 1, runs
    return runs[0]


def events(run: Path) -> list[dict]:
    return [json.loads(line) for line in (run / "steps.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def passing(tmp_path_factory):
    """One complete run against the model, shared by the tests that read its outputs."""
    results = tmp_path_factory.mktemp("results")
    before = hwplugin.tree_state()
    done = session(results)
    return done, run_dir(results), before, hwplugin.tree_state()


def test_a_run_against_the_model_passes_and_writes_its_outputs(passing):
    done, run, _, _ = passing
    assert done.returncode == 0, done.stdout[-3000:]
    assert {p.name for p in run.iterdir()} >= {"steps.jsonl", "run.log", "junit.xml", "report_spfi_encrypt.md"}
    assert len(list(run.glob("records_spfi_encrypt_*.json"))) == 1
    suite = ET.parse(run / "junit.xml").getroot().find("testsuite")
    names = [case.get("name") for case in suite.iter("testcase")]
    assert names == [f"test_hardware[TC-RS-HW-{n:02d}]" for n in (1, 2, 3, 4, 5, 6, 7, 18)]
    properties = {p.get("name"): p.get("value") for p in suite.iter("property")}
    assert properties["system"] == "spfi_encrypt" and properties["target"] == "model" and properties["seed"] == "1"
    assert "hardware test" in done.stdout and "TC-RS-HW-18   Pass" in done.stdout


def test_the_run_leaves_the_repository_and_its_submodules_as_it_found_them(passing):
    _, run, before, after = passing
    assert after == before
    env = [e for e in events(run) if e["kind"] == "env" and "when" in e["data"]]
    assert [e["data"]["when"] for e in env] == ["start", "end"]
    assert env[0]["data"]["git_status"] == env[1]["data"]["git_status"]


def test_the_step_log_holds_the_events_of_the_run(passing):
    _, run, _, _ = passing
    log = events(run)
    kinds = {e["kind"] for e in log}
    assert kinds >= {"env", "step", "read", "write", "expect", "record", "packets", "result"}
    assert all(set(e) == {"time", "test", "n", "kind", "text", "result", "data"} for e in log)
    steps = [e for e in log if e["kind"] == "step" and e["n"] == 1]
    assert [e["test"] for e in steps][:8] == [f"TC-RS-HW-{n:02d}" for n in (1, 2, 3, 4, 5, 6, 7, 18)]
    reads = [e for e in log if e["kind"] == "read" and e["data"]["name"] == "spp0.RX_PACKETS"]
    assert reads and all(e["data"]["address"] == reads[0]["data"]["address"] for e in reads)
    expects = [e for e in log
               if e["kind"] == "expect" and e["data"].get("what") == "RX_PACKETS of the Space Packet node"]
    assert expects and all(e["result"] == "pass" for e in expects)
    results = [e for e in log if e["kind"] == "result"]
    assert {e["test"]: e["result"] for e in results}["TC-RS-HW-07"] == "pass"
    assert (run / "run.log").read_text(encoding="utf-8").count(" result PASS: ") == 8


def test_the_report_has_the_results_and_the_records(passing):
    _, run, _, _ = passing
    report = (run / "report_spfi_encrypt.md").read_text(encoding="utf-8")
    assert "| TC-RS-HW-18 | Pass |  |" in report and "### Recorded values" in report
    assert "--keep-going" not in report


def test_a_results_directory_inside_the_repository_is_refused():
    done = session(ROOT / "sim_out" / "results")
    assert done.returncode != 0 and "lies inside the repository" in done.stderr
    assert not (ROOT / "sim_out" / "results").exists()


def test_the_hardware_tests_need_the_results_directory_and_refuse_k(tmp_path):
    command = [sys.executable, "-B", "-m", "pytest", str(SUITE), "--target", "model"]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode != 0 and "--results" in done.stderr
    done = session(tmp_path, "-k", "TC-RS-HW-01")
    assert done.returncode != 0 and "--tc selects" in done.stderr


def test_tc_selects_a_test_case_with_those_it_builds_on(tmp_path):
    done = session(tmp_path, "--tc", "TC-RS-HW-07")
    assert done.returncode == 0, done.stdout[-3000:]
    names = [c.get("name") for c in ET.parse(run_dir(tmp_path) / "junit.xml").getroot().iter("testcase")]
    assert names == ["test_hardware[TC-RS-HW-05]", "test_hardware[TC-RS-HW-06]", "test_hardware[TC-RS-HW-07]"]


def test_after_a_failure_the_later_test_cases_do_not_run(tmp_path):
    # TC-RS-HW-02 fails (the plugin hwfaults of sw/tests)
    done = session(tmp_path, "-p", "hwfaults", "--fault", "TC-RS-HW-02")
    assert done.returncode == 1, done.stdout[-3000:]
    run = run_dir(tmp_path)
    records = json.loads(next(run.glob("records_*.json")).read_text(encoding="utf-8"))
    assert [(r["test"], r["pass"]) for r in records["results"]] == [("TC-RS-HW-01", True), ("TC-RS-HW-02", False)]
    assert [r["test"] for r in records["not_run"]] == [f"TC-RS-HW-{n:02d}" for n in (3, 4, 5, 6, 7, 18)]
    assert all(r["reason"] == "not run: TC-RS-HW-02 failed" for r in records["not_run"])
    report = (run / "report_spfi_encrypt.md").read_text(encoding="utf-8")
    assert "| TC-RS-HW-03 | Not run | not run: TC-RS-HW-02 failed |" in report


def test_keep_going_runs_the_test_cases_whose_dependencies_passed(tmp_path):
    done = session(tmp_path, "-p", "hwfaults", "--fault", "TC-RS-HW-02", "--keep-going")
    assert done.returncode == 1
    run = run_dir(tmp_path)
    records = json.loads(next(run.glob("records_*.json")).read_text(encoding="utf-8"))
    assert [r["test"] for r in records["results"]][-1] == "TC-RS-HW-18" and records["keep_going"]
    assert "a run for diagnosis, not evidence" in (run / "report_spfi_encrypt.md").read_text(encoding="utf-8")


def test_a_run_with_bytecode_warns(tmp_path):
    done = session(tmp_path, "--tc", "TC-RS-HW-01", bytecode=True)
    assert done.returncode == 0
    warning = [e for e in events(run_dir(tmp_path)) if e["kind"] == "env" and e["data"].get("bytecode") is True]
    assert warning and "python -B -m pytest" in warning[0]["text"]


def test_the_step_log_writes_json_lines_and_text(tmp_path):
    log = steplog.StepLog()
    sinks = [steplog.JsonLines(tmp_path / "s.jsonl"), steplog.TextLines(tmp_path / "s.log")]
    log.sinks += sinks
    log.begin("TC-RS-HW-99")
    log.step("a step", ("OFB:LN-IF-01",))
    log.expect("a register", 5, 5)
    log.expect("another", 4, 5)
    for sink in sinks:
        sink.close()
    lines = [json.loads(t) for t in (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["n"], e["kind"], e["result"]) for e in lines] == [(1, "step", None), (2, "expect", "pass"),
                                                                  (3, "expect", "fail")]
    assert lines[0]["data"]["requirements"] == ["OFB:LN-IF-01"] and log.failed == 1
    assert "TC-RS-HW-99 #3 expect FAIL: another: 4, expected 5" in (tmp_path / "s.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("how", ["git missing", "no git checkout"])
def test_without_git_the_state_of_the_tree_is_unknown_and_the_run_goes_on(tmp_path, how):
    env = {"PATH": str(Path(sys.executable).parent)} if how == "git missing" else {"GIT_DIR": str(tmp_path / "none")}
    done = session(tmp_path / "results", "--tc", "TC-RS-HW-01", env=env)
    assert done.returncode == 0, done.stdout[-2000:]
    unknown = [e for e in events(run_dir(tmp_path / "results"))
               if e["kind"] == "env" and "when" in e["data"] and e["data"]["git_status"] is None]
    assert [e["data"]["when"] for e in unknown] == ["start", "end"]
    assert "tree state unknown" in unknown[0]["text"] and "WARNING" in done.stdout
