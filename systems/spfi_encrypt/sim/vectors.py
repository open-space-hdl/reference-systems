# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Stimuli and checks of the system simulation of spfi_encrypt.

The system testbench (sim/tb/spfi_encrypt_tb.vhd) joins the system to a far-end SpaceFibre node and plays the
hardware test (sw/hardware_test.py) in three steps:

1. write: the procedure of the hardware test runs against the software model of the system (sw/system_model.py)
   through a recording transport. Its requests become the operations of the far end in stimulus.txt: the start of
   the link through the MIB of the far end, then per test case the packets on the virtual channels and the polls of
   Registers.wait, which the bench repeats until the polled register has the awaited value.
2. The bench plays stimulus.txt. It records every packet that the far end receives (rx_vc<n>.txt) and the
   simulated time of its events (events.txt).
3. check: the procedure runs again through a replay transport. Every request must equal the recorded one, and the
   transport answers with the packets that the system sent in the simulation. Every pass criterion of the hardware
   test, the decryption and the tag check of every chunk included, is thereby applied to the simulated system.

The far end stands for the test equipment of the hardware test. Its virtual channels are the channels of the
equipment, and it does what the routing switch of the equipment does with the path addresses (Setup): the path
address of a command is removed before the command enters the virtual channel, and the reply address that leads a
reply is checked and removed.

Usage:
  python systems/spfi_encrypt/sim/vectors.py write <dir> [--profile sim] [--seed N] [--test TC-RS-HW-nn ...]
  python systems/spfi_encrypt/sim/vectors.py check <dir>
Exit code of check: 0 when every selected test case passes.
"""

import argparse
import contextlib
import io
import json
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "sw"))

import hardware_test as ht  # noqa: E402
from refsys.transport import Transport, TransportError  # noqa: E402
from system_model import SystemModel  # noqa: E402

STIMULUS = "stimulus.txt"
VECTORS = "vectors.json"
EVENTS = "events.txt"
WALL = "wall.txt"
REPORT = "report.txt"

# Simulated time limits of the far end (ns). The link initialises in about 100 us; a command and its reply take a
# few microseconds; the Space Packets of an exchange cross the link at several Gbit/s.
LINK_TIMEOUT_NS = 500_000
REQUEST_TIMEOUT_NS = 200_000
EXCHANGE_TIMEOUT_NS = 50_000
EXCHANGE_NS_PER_BYTE = 4


def rx_file(vc: int) -> str:
    return f"rx_vc{vc}.txt"


@dataclass(frozen=True)
class Route:
    """What the routing switch of the test equipment does for a channel: the virtual channel of the far end that
    reaches it, the path address in front of the packets sent and the reply address in front of the packets
    received."""

    vc: int
    tx_path: bytes
    rx_path: bytes


def routes(system: ht.System, setup: ht.Setup) -> dict[int, Route]:
    return {setup.rmap_channel: Route(system.rmap_vc, setup.rmap_path, setup.rmap_reply_path),
            setup.spp_channel: Route(system.spp_vc, b"", b"")}


class PollingRegisters(ht.Registers):
    """Registers whose wait tells the transport that the next read is a poll."""

    def wait(self, address: int, mask: int, value: int, timeout: float = 1.0) -> int:
        self.transport.poll = (mask, value)
        try:
            return super().wait(address, mask, value, timeout)
        finally:
            self.transport.poll = None


# --- write ----------------------------------------------------------------------------------------------------------
class Recorder(Transport):
    """Passes the requests of the procedure to the model and writes them as operations of the far end.

    Every operation is one line of stimulus.txt; its index (from 0) is the number that the bench gives in events.txt.
    The replay needs the requests as the procedure made them: they are kept in ops together with the index of the
    line that completes them.
    """

    def __init__(self, model: Transport, system: ht.System, setup: ht.Setup):
        self.model = model
        self.routes = routes(system, setup)
        self.lines: list[str] = []
        self.ops: list[dict] = []
        self.poll: tuple[int, int] | None = None
        self.polled = False

    def line(self, text: str) -> int:
        self.lines.append(text)
        return len(self.lines) - 1

    def begin(self, name: str) -> None:
        self.ops.append({"kind": "mark", "name": name, "line": self.line(f"mark {name}")})

    def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
        replies = self.model.exchange(tx_channel, packets, rx_channel, count, timeout)
        tx, rx = self.routes[tx_channel], self.routes[rx_channel]
        bare = []
        for p in packets:
            if not p.startswith(tx.tx_path):
                raise ValueError(f"packet on channel {tx_channel} without the path address {tx.tx_path.hex()}")
            bare.append(p[len(tx.tx_path):])
        op = {"kind": "exchange", "tx": tx_channel, "packets": [p.hex() for p in packets], "rx": rx_channel,
              "count": count, "poll": None}
        if self.poll is not None:
            if self.polled or len(packets) != 1 or count != 1:
                raise ValueError("a wait of the procedure needs more than one read of the model")
            self.polled = True
            mask, value = self.poll
            # the polled word is the data of a reply to a read of one register: header, 4 data bytes, CRC
            offset = len(rx.rx_path) + len(replies[0]) - 5
            op["poll"] = [offset, mask, value]
            op["line"] = self.line(f"poll {tx.vc} {REQUEST_TIMEOUT_NS} {offset} {mask:08x} {value:08x} "
                                   f"{len(bare[0])} {bare[0].hex()}")
        else:
            for p in bare:
                self.line(f"send {tx.vc} {len(p)} {p.hex()}")
            size = sum(len(p) for p in bare) + sum(len(r) for r in replies)
            limit = REQUEST_TIMEOUT_NS if count == 1 else EXCHANGE_TIMEOUT_NS + EXCHANGE_NS_PER_BYTE * size
            op["line"] = self.line(f"recv {rx.vc} {count} {limit}")
        self.ops.append(op)
        return replies

    def end_poll(self) -> None:
        self.polled = False


class RecordingRegisters(PollingRegisters):
    def wait(self, address: int, mask: int, value: int, timeout: float = 1.0) -> int:
        try:
            return super().wait(address, mask, value, timeout)
        finally:
            self.transport.end_poll()


def link_start(recorder: Recorder, system: ht.System) -> None:
    """The far end starts the link as the test equipment does (OpenFibre user guide, programming sequence): LANE_CTRL
    of every lane with LaneStart, AutoStart, TxEn and RxEn, then it waits until its link is initialised. The system
    keeps its reset values (AutoStart)."""
    ofb = ht.OFB
    start = (ofb.field("LANE_CTRL", "LANE_START", 1) | ofb.field("LANE_CTRL", "AUTO_START", 1)
             | ofb.field("LANE_CTRL", "TX_EN", 1) | ofb.field("LANE_CTRL", "RX_EN", 1))
    for lane in range(system.lanes):
        recorder.line(f"mibw {ofb.LANE_CTRL(lane):08x} {start:08x}")
    mask = ofb.field("DL_STATUS", "LINK_STATE", ofb.DL_STATUS_LINK_STATE_MASK)
    value = ofb.field("DL_STATUS", "LINK_STATE", ht.LINK_INITIALISED)
    recorder.ops.append({"kind": "link", "line": recorder.line(
        f"mibp {ofb.DL_STATUS:08x} {mask:08x} {value:08x} {LINK_TIMEOUT_NS}")})


def write(directory: Path, profile: str, seed: int, tests: list[str] | None, packets: int | None = None) -> None:
    """Records the procedure of the selected test cases against the model and writes stimulus.txt and
    vectors.json."""
    directory.mkdir(parents=True, exist_ok=True)
    count = packets or ht.PROFILES[profile].packets
    ctx = ht.make_context(seed, count)
    selected = ht.select_tests(tests)
    recorder = Recorder(SystemModel(ctx.setup), ctx.system, ctx.setup)
    link_start(recorder, ctx.system)
    # the run against the model is not a result of the simulation: its output is not shown
    with contextlib.redirect_stdout(io.StringIO()):
        results = ht.run(recorder, ctx, selected, RecordingRegisters)
    if len(results) != len(selected) or any(error for _, error in results):
        raise RuntimeError("the procedure does not pass against the model of the system")
    recorder.line("end")
    head = [f"# spfi_encrypt system simulation: profile {profile}, {count} Space Packets in TC-RS-HW-06, seed {seed}",
            "# one operation per line, numbered from 0 without the comments; written by sim/vectors.py"]
    (directory / STIMULUS).write_text("\n".join(head + recorder.lines) + "\n", encoding="utf-8")
    vectors = {"profile": profile, "seed": seed, "packets": count, "tests": [ht.test_name(t) for t in selected],
               "vcs": sorted({r.vc for r in recorder.routes.values()}), "ops": recorder.ops}
    (directory / VECTORS).write_text(json.dumps(vectors, indent=1) + "\n", encoding="utf-8")


# --- check ----------------------------------------------------------------------------------------------------------
@dataclass
class Received:
    ns: int
    data: bytes
    eep: bool


def read_records(directory: Path, vcs: list[int]) -> tuple[list[tuple[int, str, int, list[str]]],
                                                              dict[int, list[Received]]]:
    """events.txt: <ns> <kind> <line> [values]; rx_vc<n>.txt: <ns> <bytes> <eep> <hex>."""
    events = []
    path = directory / EVENTS
    if path.exists():
        for text in path.read_text(encoding="utf-8").splitlines():
            parts = text.split()
            if parts:
                events.append((int(parts[0]), parts[1], int(parts[2]), parts[3:]))
    received = {}
    for vc in vcs:
        packets = []
        path = directory / rx_file(vc)
        if path.exists():
            for text in path.read_text(encoding="utf-8").splitlines():
                parts = text.split()
                if parts:
                    data = bytes.fromhex(parts[3]) if len(parts) > 3 else b""
                    if len(data) != int(parts[1]):
                        raise ValueError(f"{rx_file(vc)}: {parts[1]} bytes announced, {len(data)} recorded")
                    packets.append(Received(int(parts[0]), data, parts[2] == "1"))
        received[vc] = packets
    return events, received


class Replay(Transport):
    """Answers the requests of the procedure with the packets that the system sent in the simulation."""

    def __init__(self, ops: list[dict], events, received: dict[int, list[Received]], routes_: dict[int, Route]):
        self.ops = deque(op for op in ops if op["kind"] != "link")
        self.routes = routes_
        self.rx = {vc: deque(packets) for vc, packets in received.items()}
        self.tries = {line: int(values[0]) for _, kind, line, values in events if kind == "poll"}
        self.timeouts = {line for _, kind, line, _ in events if kind == "timeout"}
        self.poll: tuple[int, int] | None = None

    def _next(self) -> dict:
        if not self.ops:
            raise TransportError("the procedure made more requests than were recorded")
        return self.ops.popleft()

    def begin(self, name: str) -> None:
        op = self._next()
        if op["kind"] != "mark" or op["name"] != name:
            raise TransportError(f"test case {name} where the recording has {op}")

    def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
        op = self._next()
        request = {"kind": "exchange", "tx": tx_channel, "packets": [p.hex() for p in packets], "rx": rx_channel,
                   "count": count}
        if any(op.get(k) != v for k, v in request.items()) or (op["poll"] is None) != (self.poll is None):
            raise TransportError("the request differs from the recorded one: a reply of the simulated system led the "
                                 "procedure elsewhere than the model did")
        route = self.routes[rx_channel]
        queue = self.rx[route.vc]
        if op["poll"] is not None:
            tries = self.tries.get(op["line"])
            if tries is None:
                raise TransportError(f"poll of operation {op['line']}: the register did not reach the awaited value "
                                     f"within {REQUEST_TIMEOUT_NS} ns of simulated time")
            for _ in range(tries - 1):
                queue.popleft()
            wanted = 1
        else:
            wanted = count
        if len(queue) < wanted:
            raise TransportError(f"{len(queue)} of {wanted} packets on virtual channel {route.vc} within the "
                                 f"simulated time limit")
        replies = []
        for _ in range(wanted):
            packet = queue.popleft()
            if packet.eep:
                raise TransportError(f"packet on virtual channel {route.vc} at {packet.ns} ns ended with an EEP")
            if not packet.data.startswith(route.rx_path):
                raise TransportError(f"packet on virtual channel {route.vc} at {packet.ns} ns without the reply "
                                     f"address {route.rx_path.hex()}: {packet.data[:8].hex()}")
            replies.append(packet.data[len(route.rx_path):])
        return replies

    def leftover(self) -> dict[int, int]:
        return {vc: len(q) for vc, q in self.rx.items() if q}


def check(directory: Path) -> int:
    """Replays the records of the bench with the procedure of the hardware test, writes report.txt; returns the exit
    code."""
    vectors = json.loads((directory / VECTORS).read_text(encoding="utf-8"))
    events, received = read_records(directory, vectors["vcs"])
    ctx = ht.make_context(vectors["seed"], vectors["packets"])
    selected = ht.select_tests(vectors["tests"])
    replay = Replay(vectors["ops"], events, received, routes(ctx.system, ctx.setup))
    results = ht.run(replay, ctx, selected, PollingRegisters)
    problems = []
    kinds = {kind for _, kind, _, _ in events}
    if not events:
        problems.append("the bench recorded no event: the simulation did not start (see the log of the simulator)")
    elif "end" not in kinds and "timeout" not in kinds:
        problems.append("the simulation did not reach the end of the stimulus")
    if "link" not in {op["kind"] for op in vectors["ops"]} or not any(
            kind == "mibp" for _, kind, _, _ in events):
        problems.append("the link of the far end did not initialise")
    passed = len(results) == len(selected) and all(error is None for _, error in results)
    if passed and replay.leftover():
        problems.append(f"packets that no request awaited: {replay.leftover()} (virtual channel: count)")
    report = make_report(vectors, events, results, problems, read_wall(directory))
    (directory / REPORT).write_text(report, encoding="utf-8")
    print(report, end="")
    return 0 if passed and not problems else 1


def read_wall(directory: Path) -> dict[int, float]:
    """Wall-clock seconds from the start of the simulation at which the runner saw an event: <line> <seconds>."""
    path = directory / WALL
    if not path.exists():
        return {}
    return {int(a): float(b) for a, b in (t.split() for t in path.read_text(encoding="utf-8").splitlines() if t)}


def make_report(vectors, events, results, problems, wall) -> str:
    times = {line: ns for ns, kind, line, _ in events if kind in ("mark", "mibp", "end", "timeout")}
    ready = next((ns for ns, kind, _, _ in events if kind == "ready"), None)
    marks = [op for op in vectors["ops"] if op["kind"] == "mark"]
    link = next((op["line"] for op in vectors["ops"] if op["kind"] == "link"), None)
    stop = max((ns for ns, kind, _, _ in events if kind in ("end", "timeout")), default=None)
    stop_line = max((line for _, kind, line, _ in events if kind in ("end", "timeout")), default=None)
    out = [f"spfi_encrypt system simulation: profile {vectors['profile']}, {vectors['packets']} Space Packets in "
           f"TC-RS-HW-06, seed {vectors['seed']}", ""]
    if ready is not None and link in times:
        out.append(f"Far end out of reset at {ready / 1000:.1f} us, link initialised at {times[link] / 1000:.1f} us "
                   "(simulated time)")
    out += ["", f"{'Test case':<13} {'Result':<7} {'Simulated':>12} {'Wall clock':>12}"]
    verdict = {name: ("PASS" if error is None else "FAIL") for name, error in results}
    bounds = [(op["name"], op["line"]) for op in marks] + [("end", stop_line)]
    for (name, line), (_, after) in zip(bounds, bounds[1:]):
        start = times.get(line)
        end = times.get(after) if after is not None else None
        end = end if end is not None else stop
        sim = f"{(end - start) / 1000:.1f} us" if start is not None and end is not None else "-"
        w0, w1 = wall.get(line), wall.get(after) if after is not None else None
        wall_text = f"{(w1 - w0) / 60:.1f} min" if w0 is not None and w1 is not None else "-"
        out.append(f"{name:<13} {verdict.get(name, 'not run'):<7} {sim:>12} {wall_text:>12}")
    if stop is not None:
        out += ["", f"Total simulated time {stop / 1000:.1f} us"]
    if wall and stop_line in wall:
        out.append(f"Total wall-clock time {wall[stop_line] / 60:.1f} min")
    errors = [f"{name}: {error}" for name, error in results if error]
    if errors or problems:
        out += [""] + errors + problems
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("write", help="write stimulus.txt and vectors.json")
    w.add_argument("directory", type=Path)
    w.add_argument("--profile", choices=ht.PROFILES, default="sim")
    w.add_argument("--seed", type=int, default=1)
    w.add_argument("--packets", type=int, help="Space Packets of TC-RS-HW-06 (default: of the profile)")
    w.add_argument("--test", action="append", help="only this test case and those it builds on")
    c = sub.add_parser("check", help="check the records of the bench, write report.txt")
    c.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "write":
        write(args.directory, args.profile, args.seed, args.test, args.packets)
        return 0
    return check(args.directory)


if __name__ == "__main__":
    sys.exit(main())
