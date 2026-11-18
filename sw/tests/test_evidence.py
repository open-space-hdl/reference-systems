# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of what the outputs of a run say about its evidence (sw/refsys/hwplugin.py, tools/render_records.py,
sw/refsys/systest.py): the device image of a run against the device with its SHA-256 and build information, the
working tree without its ignored files, the count of the test cases by result, the requirements as those that a test
case exercises, and the error registers of a test case that provokes errors, the expected ones apart."""

import hashlib
import sys
from pathlib import Path

import pytest

from refsys import hwplugin, steplog, systest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import render_records  # noqa: E402

RECORDS = {"system": "spfi_compress", "started": "2026-11-17 14:02:35", "finished": "2026-11-17 14:07:11",
           "target": "device", "profile": "lab", "seed": 1, "records": [], "images": [],
           "results": [{"test": "TC-RS-HW-01", "pass": True, "error": None},
                       {"test": "TC-RS-HW-12", "pass": False, "error": "a check"}],
           "not_run": [{"test": "TC-RS-HW-07", "reason": "not run: TC-RS-HW-06 did not pass"}],
           "requirements": {"TC-RS-HW-01": ["OFB:LN-IF-01..06"]}}


def events(log: steplog.StepLog) -> list[dict]:
    out = []
    log.sinks.append(out.append)
    return out


def test_the_device_image_is_recorded_with_its_digest_and_build_information(tmp_path):
    image = tmp_path / "spfi_compress.pdi"
    image.write_bytes(b"\x00\x01device image")
    (tmp_path / "build_info.txt").write_text("spfi_compress, step all\nrepository 0123abc\n", encoding="utf-8")
    log = steplog.StepLog()
    seen = events(log)
    got = hwplugin.device_image(log, str(image))
    digest = hashlib.sha256(b"\x00\x01device image").hexdigest()
    assert got == {"name": "spfi_compress.pdi", "bytes": 14, "sha256": digest,
                   "build_info": "spfi_compress, step all\nrepository 0123abc\n"}
    assert seen[0]["text"] == f"device image spfi_compress.pdi, 14 bytes, SHA-256 {digest}"
    assert str(tmp_path) not in str(got)                 # no path in the records


def test_a_run_without_the_device_image_says_so(tmp_path):
    log = steplog.StepLog()
    seen = events(log)
    assert hwplugin.device_image(log, None) is None
    assert seen[0]["text"] == "device image: not identified (no --image)"
    image = tmp_path / "x.pdi"
    image.write_bytes(b"x")
    hwplugin.device_image(log, str(image))
    assert seen[-1]["text"].endswith("; no build_info.txt next to it")


def test_the_report_names_the_device_image_or_says_that_it_is_not_identified():
    rows = render_records.run_table(RECORDS)
    assert rows[-1] == "| Device image | not identified: the run got no `--image` |"
    data = RECORDS | {"device_image": {"name": "a.pdi", "bytes": 1, "sha256": "ab" * 32, "build_info": "step all\n"}}
    assert render_records.run_table(data)[-1] == f"| Device image | a.pdi, SHA-256 `{'ab' * 32}` |"
    assert "### Device image\n\n```text\nstep all\n```" in render_records.render(data)
    assert not [r for r in render_records.run_table(RECORDS | {"target": "system model"}) if "Device image" in r]


def test_the_report_counts_the_test_cases_and_calls_the_requirements_exercised():
    report = render_records.render(RECORDS)
    assert "### Results per test case\n\n3 test cases: 1 passed, 1 failed, 1 not run.\n" in report
    assert "### Requirements exercised per test case" in report and "### Requirements per test case" not in report
    assert "verified against its own criterion by the regression of its core" in report


def test_the_working_tree_counts_its_changes_and_records_its_ignored_files(monkeypatch):
    log = steplog.StepLog()
    seen = events(log)
    monkeypatch.setattr(hwplugin, "tree_state", lambda: {".": ["!! sw/refsys/__pycache__/"],
                                                         "cores/opencompact": ["!! tools/__pycache__/"]})
    hwplugin.environment(log, "end")
    assert seen[0]["text"] == ("working tree at the end of the run: clean (git status --porcelain empty); 2 ignored "
                               "entries, recorded")
    assert seen[0]["data"]["git_status"]["."] == ["!! sw/refsys/__pycache__/"]
    monkeypatch.setattr(hwplugin, "tree_state", lambda: {".": [" M sw/refsys/systest.py", "!! a/"], "cores/x": []})
    hwplugin.environment(log, "end")
    assert seen[1]["text"] == ("working tree at the end of the run: 1 changed entries in .; 1 ignored entries, "
                               "recorded")


def test_the_errors_of_a_test_case_that_provokes_them_are_recorded_apart():
    state = {"ofb0.DL_ERRORS": 0x20, "ofb0.ML_MISALIGNED": 3, "ofb0.DL_RETRIES": 0, "rmap0.EVENTS": 0}
    text = systest.error_record(state, {"ofb0.DL_ERRORS": 0x20}, {"ofb0.ML_MISALIGNED": 0xFFFFFFFF,
                                                                  "ofb0.DL_RETRIES": 0xFFFFFFFF})
    assert text == "expected ofb0.DL_ERRORS 0x00000020; allowed to move: ofb0.ML_MISALIGNED 0x00000003; others: all 0"
    assert systest.error_record({"rmap0.EVENTS": 4}, {}, {}) == ("expected none; allowed to move: none; others: "
                                                                 "rmap0.EVENTS 0x00000004")


@pytest.mark.parametrize("missing", [True, False])
def test_an_image_that_is_no_file_is_refused(tmp_path, missing):
    path = tmp_path / ("none.pdi" if missing else "")
    with pytest.raises(pytest.UsageError, match="no such file"):
        hwplugin.device_image(steplog.StepLog(), str(path))
