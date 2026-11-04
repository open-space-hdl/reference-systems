# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of the judgement of the Space Packets that come back around a pipeline reset during traffic
(sw/refsys/resettest.py) and of the end markers of received packets (sw/refsys/transport.py, the replay of a system
simulation in sw/refsys/sysvectors.py)."""

from types import SimpleNamespace

import pytest

from refsys import resettest, steplog, systest, sysvectors
from refsys.transport import Packet, TransportError, end_of

A = [bytes([0xFE, 2, 0, 0]) + bytes([1, n]) * 8 for n in range(3)]     # three link packets of APID 1
B = [bytes([0xFE, 2, 0, 0]) + bytes([2, n]) * 8 for n in range(2)]     # two link packets of APID 2


def context():
    return SimpleNamespace(records=[], recorded=[], log=steplog.StepLog(), current="TC-RS-HW-22")


def cut(packet: bytes, size: int, end: str | None = "EEP") -> bytes:
    """packet cut after size bytes by the pipeline reset: the byte 0x00 of the EEP beat follows."""
    return Packet(packet[:size] + b"\x00", end) if end else packet[:size] + b"\x00"


def test_a_cut_packet_is_a_proper_prefix_and_one_zero_byte():
    assert resettest.is_cut(A[0][:5] + b"\x00", A[0])
    assert resettest.is_cut(b"\x00", A[0])                              # cut before its first byte
    assert not resettest.is_cut(A[0] + b"\x00", A[0])                   # not a proper prefix
    assert not resettest.is_cut(A[0][:5] + b"\x01", A[0])
    assert not resettest.is_cut(A[0][:4] + b"\x00", A[1][:3] + b"\x55" + A[1][4:])


def test_complete_packets_of_every_apid_in_their_order_pass_and_are_counted():
    ctx = context()
    seen = resettest.judge_traffic(ctx, [A[0], B[0], A[1], B[1]], {1: A, 2: B}, "TC-RS-HW-22")
    assert seen == {1: 2, 2: 2}
    assert "TC-RS-HW-22: APID 1: 2 Space Packets equal to the model, of 3 that the data sent can give" in ctx.records
    assert "TC-RS-HW-22: no Space Packet cut by the pipeline reset" in ctx.records


@pytest.mark.parametrize("end", ["EEP", None])
def test_the_last_packet_may_be_cut_by_the_reset(end):
    ctx = context()
    seen = resettest.judge_traffic(ctx, [A[0], B[0], cut(A[1], 7, end)], {1: A, 2: B}, "TC-RS-HW-22")
    assert seen == {1: 1, 2: 1}
    marker = end or "not reported by the transport"
    assert ctx.records[-1] == ("TC-RS-HW-22: Space Packet 2 cut by the pipeline reset after 7 bytes of the next Space "
                               f"Packet of APID 1 and the byte 0x00 of the EEP beat, end marker {marker}")


@pytest.mark.parametrize("received, message", [
    ([cut(A[0], 7), B[0]], "Space Packet 1 arrived after the packet that the pipeline reset cut"),
    ([A[1]], "Space Packet 0 of 20 bytes (end marker None) is not the next Space Packet of an output APID"),
    ([cut(A[0], 7, "EOP")], "end marker EOP"),
    ([Packet(A[0] + b"\x00", "EEP")], "is not the next Space Packet of an output APID nor that packet cut"),
    ([A[0], A[1], A[2], cut(A[0], 6)], "Space Packet 3 of 7 bytes (end marker EEP)"),      # APID 1 has no more
])
def test_a_packet_out_of_place_fails(received, message):
    with pytest.raises(systest.TestFailed, match=message.replace("(", r"\(").replace(")", r"\)")):
        resettest.judge_traffic(context(), received, {1: A, 2: B}, "TC-RS-HW-22")


def test_before_the_reset_no_packet_may_be_cut():
    with pytest.raises(systest.TestFailed, match="is not the next Space Packet of an output APID$"):
        resettest.judge_traffic(context(), [cut(A[0], 7)], {1: A}, "before the pipeline reset", cut=False)


def test_a_received_packet_carries_its_end_marker():
    packet = Packet(b"\x01\x02", "EEP")
    assert packet == b"\x01\x02" and isinstance(packet, bytes) and end_of(packet) == "EEP"
    assert end_of(b"\x01\x02") is None and Packet(b"\x01").end is None


def test_the_replay_passes_an_eep_on_the_channel_of_the_space_packets_and_refuses_it_on_that_of_rmap():
    routes = {0: sysvectors.Route(0, b"\x03", b"\x01"), 1: sysvectors.Route(1, b"", b"")}
    received = {0: [sysvectors.Received(10, b"\x01\x05\x06", True)],
                1: [sysvectors.Received(20, b"\xfe\x02\x00", True), sysvectors.Received(30, b"\xfe\x02\x01", False)]}
    replay = sysvectors.Replay([], [], received, routes)
    packets = replay._take(1, 2)
    assert packets == [b"\xfe\x02\x00", b"\xfe\x02\x01"] and [p.end for p in packets] == ["EEP", "EOP"]
    with pytest.raises(TransportError, match="packet on virtual channel 0 at 10 ns ended with an EEP"):
        replay._take(0, 1)
