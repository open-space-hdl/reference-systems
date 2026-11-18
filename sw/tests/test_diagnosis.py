# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of what a run tells about a failure (sw/refsys/systest.py, sw/refsys/steplog.py, sw/refsys/regs.py): the
fields of a register value, the one event of a wait and its text on a timeout, and the state of the system that a
failing test case leaves in the step log (failure_snapshot). The far end is the system model of spfi_encrypt."""

import sys
from pathlib import Path

import pytest

from refsys import steplog, systest
from refsys.systest import OFB, OMAP

ROOT = Path(__file__).resolve().parents[2]
ENCRYPT = ROOT / "systems" / "spfi_encrypt" / "sw"


@pytest.fixture
def run():
    """The register access of a run of spfi_encrypt against its system model, with a step log that keeps its events
    (log.events) and the context of the run."""
    ht, model = systest.system_modules(ENCRYPT, "hardware_test", "system_model")
    ctx = ht.make_context(seed=1, packets=24)
    events = []
    ctx.log = steplog.StepLog()
    ctx.log.sinks.append(events.append)
    ctx.log.events = events
    regs = systest.prepare(model.SystemModel(ctx.setup), ctx, list(ht.TESTS))
    token = systest.ACTIVE_LOG.set(ctx.log)
    yield regs, ctx
    systest.ACTIVE_LOG.reset(token)


def kinds(ctx) -> list[str]:
    return [e["kind"] for e in ctx.log.events]


def test_the_fields_of_a_register_value():
    assert OFB.decode("DL_STATUS", 0x343) == "LINK_STATE 3, RX_ERR_STATE 0, WORD_ID_STATE 4, ERB_EMPTY 1, TX_READY 1"
    assert OFB.decode("DL_ERRORS", 0x20, nonzero=True) == "FAR_END_LINK_RESET 1"
    assert OFB.decode("LANE_TIMEOUT_COUNT", 0x00060005) == "COUNT 5, RX_RESETS 6"
    assert OFB.covering("DL_ERRORS", 0x20) == ["FAR_END_LINK_RESET"]
    assert OFB.covering("ML_STATUS", 0xFF) == ["DATA_SENDING", "DATA_RECEIVING"]
    # the fields of VC_CFG are not fields of a register VC
    assert list(OFB.fields("VC_CFG")) == ["PRIORITY", "CONTINUOUS", "VN"] and "CFG_VN" not in OFB.fields("VC")
    assert OMAP.decode("ID", 0x1234) == "ID 0x1234"


def test_a_passing_check_makes_no_event_and_is_counted():
    log = steplog.StepLog()
    events = []
    log.sinks.append(events.append)
    log.begin("TC-RS-HW-18")
    log.check(True, "a criterion")
    log.begin("TC-RS-HW-18 TC-RS-HW-02")
    log.check(True, "a criterion of the second run")
    log.check(False, "a failing criterion")
    assert [(e["kind"], e["result"], e["text"]) for e in events] == [("check", "fail", "a failing criterion")]
    assert log.passed_checks("TC-RS-HW-18") == 2 and log.passed_checks("TC-RS-HW-1") == 0 and log.failed == 1


def test_a_wait_is_one_event_with_its_polls_and_names_the_register_and_its_fields(run):
    regs, ctx = run
    base = ctx.system.base(ctx.system.port)
    lanes = OFB.field("ML_STATUS", "DATA_SENDING", 0xF)
    word = regs.wait(base + OFB.ML_STATUS, lanes, lanes)
    assert word & lanes == lanes and kinds(ctx) == ["wait"]
    event = ctx.log.events[0]
    assert event["data"]["polls"] == regs.polls == 1 and event["data"]["name"] == "ofb0.ML_STATUS"
    assert event["text"].startswith(f"ofb0.ML_STATUS.DATA_SENDING (0x{base + OFB.ML_STATUS:08X}) = 0x")


def test_a_wait_that_times_out_says_which_field_how_long_and_how_many_polls(run):
    regs, ctx = run
    base = ctx.system.base(ctx.system.port)
    reset = OFB.field("DL_ERRORS", "FAR_END_LINK_RESET", 1)
    with pytest.raises(systest.TestFailed) as failure:
        regs.wait(base + OFB.DL_ERRORS, reset, reset, timeout=0.05)
    text = str(failure.value)
    assert text.startswith(f"ofb0.DL_ERRORS.FAR_END_LINK_RESET (0x{base + OFB.DL_ERRORS:08X}) = 0x00000000 after ")
    assert " polls in " in text and text.endswith("s, expected 0x00000020 under 0x00000020")
    # no read event per poll: the failed check is the only event
    assert kinds(ctx) == ["check"] and regs.polls > 1


def test_a_failing_test_case_leaves_the_state_of_the_system_in_the_log(run):
    regs, ctx = run

    def failing(regs, ctx):
        """TC-RS-HW-98: fails after a read."""
        regs.read(ctx.system.base(ctx.system.port) + OFB.ID)
        systest.check(False, "the criterion of the test case")

    with pytest.raises(systest.TestFailed, match="the criterion of the test case"):
        systest.run_one(regs, ctx, failing)
    snapshots = [e["text"] for e in ctx.log.events if e["kind"] == "snapshot"]
    assert len(snapshots) == 2 + ctx.system.lanes
    assert snapshots[0].startswith("ofb0 DL_STATUS 0x") and "LINK_STATE 3" in snapshots[0]
    assert "DL_ERRORS 0x00000000 ()" in snapshots[0] and "ML_STATUS 0x" in snapshots[0]
    assert snapshots[1].startswith("ofb0 lane 0: LANE_STATUS 0x") and "LANE_TIMEOUT_COUNT 0x" in snapshots[1]
    assert snapshots[-1].startswith("error registers: ")
    assert [s["text"] for s in ctx.snapshots] == snapshots and {s["test"] for s in ctx.snapshots} == {"TC-RS-HW-98"}


def test_a_snapshot_that_cannot_read_ends_with_the_reason_and_keeps_the_failure(run, monkeypatch):
    regs, ctx = run

    def failing(regs, ctx):
        """TC-RS-HW-98: fails, and the RMAP target answers no more."""
        monkeypatch.setattr(regs.transport, "request", lambda *a, **k: (_ for _ in ()).throw(
            systest.TransportError("no reply within 5.0 s")))
        systest.check(False, "the criterion of the test case")

    with pytest.raises(systest.TestFailed, match="the criterion of the test case"):
        systest.run_one(regs, ctx, failing)
    assert [s["text"] for s in ctx.snapshots] == ["snapshot ends: TransportError: no reply within 5.0 s"]


def test_the_text_log_leaves_out_the_register_accesses(tmp_path):
    log = steplog.StepLog()
    text = steplog.TextLines(tmp_path / "run.log", steplog.READABLE)
    log.sinks.append(text)
    log.begin("TC-RS-HW-99")
    log.access("read", 0x70010, [0x343], "ofb0.DL_STATUS")
    log.access("write", 0x70014, [0xFFFFFFFF], "ofb0.DL_ERRORS")
    log.emit("note", "channel 0: 0 packets discarded")
    log.check(False, "a criterion")
    text.close()
    lines = (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[1] for line in lines] == ["TC-RS-HW-99 #3 note: channel 0: 0 packets discarded",
                                                         "TC-RS-HW-99 #4 check FAIL: a criterion"]


def test_the_recovery_reads_the_channels_and_clears_the_errors(run):
    regs, ctx = run
    systest.recover(regs, ctx, "TC-RS-HW-97")
    steps = [r["text"] for r in ctx.recoveries]
    assert steps[0] == "channels read until quiet: 0 packets discarded on channel 0, 0 packets discarded on channel 1"
    assert steps[1].startswith("health check passed") and steps[2].startswith("error registers cleared: ")
    assert len(steps) == 3 and all(r["after"] == "TC-RS-HW-97" for r in ctx.recoveries)


def test_a_system_that_stays_unhealthy_stops_the_run(run):
    regs, ctx = run
    port = ctx.system.base(ctx.system.port)
    # the link stays down, also after the link reset of the far end
    regs.transport.regs[port + OFB.DL_STATUS] &= ~OFB.field("DL_STATUS", "LINK_STATE", 3)
    with pytest.raises(systest.TestFailed, match="the system is not healthy after TC-RS-HW-97 failed"):
        systest.recover(regs, ctx, "TC-RS-HW-97")
    steps = [r["text"] for r in ctx.recoveries]
    assert "; the far end resets the link" in steps[1] and steps[2] == "far end of the system model: link reset"
    assert steps[-1].startswith("health check failed again") and "DL_STATUS" in steps[-1]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
