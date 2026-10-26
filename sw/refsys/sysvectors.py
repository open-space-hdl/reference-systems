# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Stimuli and checks of the system simulations of the reference systems.

The system testbench of a system (systems/<system>/sim/tb/) joins the system to a far-end SpaceFibre node and plays
the hardware test of the system (systems/<system>/sw/hardware_test.py) in three steps:

1. write: the procedure of the hardware test runs against the software model of the system (sw/system_model.py)
   through a recording transport. Its requests become the operations of the far end in stimulus.txt: the wait for
   the link, which the system starts, then per test case the packets on the virtual channels, the polls of
   Registers.wait, which the bench repeats until the polled register has the awaited value, and the receptions until
   a channel is quiet.
2. The bench plays stimulus.txt. It records every packet that the far end receives (rx_vc<n>.txt) and the
   simulated time of its events (events.txt).
3. check: the procedure runs again through a replay transport. Every request must equal the recorded one, and the
   transport answers with the packets that the system sent in the simulation. Every pass criterion of the hardware
   test is thereby applied to the simulated system.

The far end stands for the test equipment of the hardware test. Its virtual channels are the channels of the
equipment, and it does what the routing switch of the equipment does with the path addresses (Setup): the path
address of a command is removed before the command enters the virtual channel, and the reply address that leads a
reply is checked and removed.

Operations of stimulus.txt, one per line, numbered from 0 without the comments:

  mark <name>                                  start of a test case
  mibw <address> <data>                        write of a register of the far end
  mibp <address> <mask> <value> <limit ns>     reads a register of the far end until it has the value
  send <vc> <bytes> <packet>                   sends a packet on a virtual channel
  recv <vc> <count> <limit ns>                 waits for count more packets on a virtual channel
  poll <vc> <limit ns> <offset> <mask> <value> <bytes> <packet>
                                               sends the packet until the word at the offset of the reply has the
                                               value under the mask
  quiet <vc> <quiet ns> <limit ns>             receives on a virtual channel until no packet arrives for quiet ns
  end                                          end of the stimulus
"""

import argparse
import contextlib
import io
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from refsys import systest
from refsys.transport import Transport, TransportError

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
# A procedure that knows how long the system works on what it sends (expect_busy) adds that time to the limit of its
# next exchange, in cycles of the system clock; a cycle counts BUSY_NS_PER_CYCLE ns (100 MHz, the slowest system clock
# of the benches) with a margin of a quarter
BUSY_NS_PER_CYCLE = 10
# A channel is quiet when no packet arrived for QUIET_NS; the reception ends at the latest after QUIET_LIMIT_NS
QUIET_NS = 100_000
QUIET_LIMIT_NS = 20_000_000


def rx_file(vc: int) -> str:
    return f"rx_vc{vc}.txt"


@dataclass(frozen=True)
class Bench:
    """What the simulation of one system needs of its hardware test.

    context(seed, profile, size) makes the context of a run (size: a size of the run other than that of the profile,
    or None); fields(context) gives the sizes that vectors.json keeps, and restore(vectors) makes the context again
    from vectors.json; describe(vectors) names the size in the headers; model(context) is the software model of the
    system.
    """

    name: str
    ht: ModuleType
    model: Callable[[object], Transport]
    context: Callable[[int, str, int | None], object]
    fields: Callable[[object], dict]
    restore: Callable[[dict], object]
    describe: Callable[[dict], str]


@dataclass(frozen=True)
class Route:
    """What the routing switch of the test equipment does for a channel: the virtual channel of the far end that
    reaches it, the path address in front of the packets sent and the reply address in front of the packets
    received."""

    vc: int
    tx_path: bytes
    rx_path: bytes


def routes(system, setup) -> dict[int, Route]:
    return {setup.rmap_channel: Route(system.rmap_vc, setup.rmap_path, setup.rmap_reply_path),
            setup.spp_channel: Route(system.spp_vc, b"", b"")}


class PollingRegisters(systest.Registers):
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
    line that completes them. Consecutive receptions on a channel up to the first one without a packet are one
    operation quiet.
    """

    def __init__(self, model: Transport, system, setup):
        self.model = model
        self.routes = routes(system, setup)
        self.lines: list[str] = []
        self.ops: list[dict] = []
        self.poll: tuple[int, int] | None = None
        self.polled = False
        self.quiet: int | None = None          # channel of the reception that is going on
        self.busy_cycles = 0                   # system clock cycles of the work of the next exchange (expect_busy)

    def line(self, text: str) -> int:
        self.lines.append(text)
        return len(self.lines) - 1

    def begin(self, name: str) -> None:
        self.quiet = None
        self.ops.append({"kind": "mark", "name": name, "line": self.line(f"mark {name}")})

    def expect_busy(self, cycles: int) -> None:
        """The system works cycles cycles of its clock on the packets of the next exchange (for example compressors
        at the pipeline rate of their predictor): its time limit grows by that time."""
        self.busy_cycles = cycles

    def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
        self.quiet = None
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
            limit += self.busy_cycles * BUSY_NS_PER_CYCLE * 5 // 4
            self.busy_cycles = 0
            op["line"] = self.line(f"recv {rx.vc} {count} {limit}")
        self.ops.append(op)
        return replies

    def receive(self, channel, timeout):
        packet = self.model.receive(channel, timeout)
        if self.quiet != channel:
            rx = self.routes[channel]
            self.ops.append({"kind": "quiet", "rx": channel,
                             "line": self.line(f"quiet {rx.vc} {QUIET_NS} {QUIET_LIMIT_NS}")})
            self.quiet = channel
        if packet is None:
            self.quiet = None
        return packet

    def end_poll(self) -> None:
        self.polled = False


class RecordingRegisters(PollingRegisters):
    def wait(self, address: int, mask: int, value: int, timeout: float = 1.0) -> int:
        try:
            return super().wait(address, mask, value, timeout)
        finally:
            self.transport.end_poll()


def link_wait(recorder: Recorder) -> None:
    """The system starts the link after the reset (LaneStart_g of ofb0). The far end keeps its reset values
    (AutoStart), as the port of the test equipment is set, and waits until its link is initialised."""
    ofb = systest.OFB
    mask = ofb.field("DL_STATUS", "LINK_STATE", ofb.DL_STATUS_LINK_STATE_MASK)
    value = ofb.field("DL_STATUS", "LINK_STATE", systest.LINK_INITIALISED)
    recorder.ops.append({"kind": "link", "line": recorder.line(
        f"mibp {ofb.DL_STATUS:08x} {mask:08x} {value:08x} {LINK_TIMEOUT_NS}")})


def write(bench: Bench, directory: Path, profile: str, seed: int, tests: list[str] | None,
          size: int | None = None) -> None:
    """Records the procedure of the selected test cases against the model and writes stimulus.txt and
    vectors.json."""
    ht = bench.ht
    directory.mkdir(parents=True, exist_ok=True)
    ctx = bench.context(seed, profile, size)
    selected = ht.select_tests(tests)
    recorder = Recorder(bench.model(ctx), ctx.system, ctx.setup)
    link_wait(recorder)
    # the run against the model is not a result of the simulation: its output is not shown
    with contextlib.redirect_stdout(io.StringIO()):
        results = ht.run(recorder, ctx, selected, RecordingRegisters)
    if len(results) != len(selected) or any(error for _, error in results):
        raise RuntimeError("the procedure does not pass against the model of the system")
    recorder.line("end")
    vectors = {"profile": profile, "seed": seed, **bench.fields(ctx), "tests": [ht.test_name(t) for t in selected],
               "vcs": sorted({r.vc for r in recorder.routes.values()}), "ops": recorder.ops}
    head = [f"# {bench.name} system simulation: profile {profile}, {bench.describe(vectors)}, seed {seed}",
            "# one operation per line, numbered from 0 without the comments; written by sim/vectors.py"]
    (directory / STIMULUS).write_text("\n".join(head + recorder.lines) + "\n", encoding="utf-8")
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
        self.quiet_counts = {line: int(values[0]) for _, kind, line, values in events if kind == "quiet"}
        self.timeouts = {line for _, kind, line, _ in events if kind == "timeout"}
        self.poll: tuple[int, int] | None = None
        self.quiet: tuple[int, int] | None = None      # channel and packets left of the reception going on

    def _next(self) -> dict:
        self.quiet = None
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
        if op["poll"] is not None:
            tries = self.tries.get(op["line"])
            if tries is None:
                raise TransportError(f"poll of operation {op['line']}: the register did not reach the awaited value "
                                     f"within {REQUEST_TIMEOUT_NS} ns of simulated time")
            queue = self.rx[self.routes[rx_channel].vc]
            for _ in range(tries - 1):
                queue.popleft()
            return self._take(rx_channel, 1)
        return self._take(rx_channel, count)

    def receive(self, channel, timeout):
        if self.quiet is None or self.quiet[0] != channel:
            op = self._next()
            if op["kind"] != "quiet" or op["rx"] != channel:
                raise TransportError("the request differs from the recorded one: a reply of the simulated system led "
                                     "the procedure elsewhere than the model did")
            count = self.quiet_counts.get(op["line"])
            if count is None:
                raise TransportError(f"reception of operation {op['line']}: virtual channel "
                                     f"{self.routes[channel].vc} was not quiet within {QUIET_LIMIT_NS} ns of "
                                     "simulated time")
            self.quiet = (channel, count)
        channel_, left = self.quiet
        if left == 0:
            self.quiet = None
            return None
        self.quiet = (channel_, left - 1)
        return self._take(channel, 1)[0]

    def _take(self, rx_channel: int, wanted: int) -> list[bytes]:
        route = self.routes[rx_channel]
        queue = self.rx[route.vc]
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


def check(bench: Bench, directory: Path) -> int:
    """Replays the records of the bench with the procedure of the hardware test, writes report.txt; returns the exit
    code."""
    ht = bench.ht
    vectors = json.loads((directory / VECTORS).read_text(encoding="utf-8"))
    events, received = read_records(directory, vectors["vcs"])
    ctx = bench.restore(vectors)
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
    report = make_report(bench, vectors, events, results, problems, read_wall(directory), ctx.records)
    (directory / REPORT).write_text(report, encoding="utf-8")
    print(report, end="")
    return 0 if passed and not problems else 1


def read_wall(directory: Path) -> dict[int, float]:
    """Wall-clock seconds from the start of the simulation at which the runner saw an event: <line> <seconds>."""
    path = directory / WALL
    if not path.exists():
        return {}
    return {int(a): float(b) for a, b in (t.split() for t in path.read_text(encoding="utf-8").splitlines() if t)}


def make_report(bench: Bench, vectors, events, results, problems, wall, records=()) -> str:
    times = {line: ns for ns, kind, line, _ in events if kind in ("mark", "mibp", "end", "timeout")}
    ready = next((ns for ns, kind, _, _ in events if kind == "ready"), None)
    marks = [op for op in vectors["ops"] if op["kind"] == "mark"]
    link = next((op["line"] for op in vectors["ops"] if op["kind"] == "link"), None)
    stop = max((ns for ns, kind, _, _ in events if kind in ("end", "timeout")), default=None)
    stop_line = max((line for _, kind, line, _ in events if kind in ("end", "timeout")), default=None)
    out = [f"{bench.name} system simulation: profile {vectors['profile']}, {bench.describe(vectors)}, seed "
           f"{vectors['seed']}", ""]
    if ready is not None and link in times:
        out.append(f"Transmitters of the far end ready at {ready / 1000:.1f} us, link initialised at "
                   f"{times[link] / 1000:.1f} us (simulated time)")
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
    if records:
        out += ["", "Recorded values (no pass criteria)"] + [f"  {text}" for text in records]
    errors = [f"{name}: {error}" for name, error in results if error]
    if errors or problems:
        out += [""] + errors + problems
    return "\n".join(out) + "\n"


# --- stand-in for the bench -----------------------------------------------------------------------------------------
def play(directory: Path, model: Transport, system, setup, drop_last: bool = False,
         reply_address: bytes | None = None) -> None:
    """Plays stimulus.txt as the bench does, against the software model at the level of the virtual channels of the
    far end, and writes events.txt and rx_vc<n>.txt in the format of the bench (for the tests without a simulator).
    drop_last loses the last packet of the first exchange of several packets; reply_address replaces the reply
    address in front of every received packet."""
    by_vc = {r.vc: (ch, r) for ch, r in routes(system, setup).items()}
    events = ["900 ready -1"]
    rx = {vc: [] for vc in by_vc}
    pending = {vc: [] for vc in by_vc}
    ns, index = 1000, -1

    def record(vc: int, reply: bytes) -> None:
        route = by_vc[vc][1]
        data = (route.rx_path if reply_address is None else reply_address) + reply
        rx[vc].append(f"{ns} {len(data)} 0 {data.hex()}")

    for text in (directory / STIMULUS).read_text(encoding="utf-8").splitlines():
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
            for reply in model.exchange(channel, [route.tx_path + p for p in packets], channel, count):
                record(vc, reply)
            if f[0] == "poll":
                events.append(f"{ns} poll {index} 1")
            elif drop_last and count > 1:
                rx[vc].pop()
                events.append(f"{ns + REQUEST_TIMEOUT_NS} timeout {index}")
                break
        elif f[0] == "quiet":
            vc = int(f[1])
            count = 0
            while (reply := model.receive(by_vc[vc][0], 0.0)) is not None:
                record(vc, reply)
                count += 1
            events.append(f"{ns} quiet {index} {count}")
        elif f[0] == "end":
            events.append(f"{ns} end {index}")
        else:
            assert f[0] == "mibw" and len(f) == 3
    (directory / EVENTS).write_text("\n".join(events) + "\n", encoding="utf-8")
    for vc, lines in rx.items():
        (directory / rx_file(vc)).write_text("".join(f"{t}\n" for t in lines), encoding="utf-8")


def main(bench: Bench, description: str, size_help: str | None = None) -> int:
    """Command line of the vectors.py of a system; size_help: the meaning of --size (None: no such option)."""
    parser = argparse.ArgumentParser(description=description)
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("write", help="write stimulus.txt and vectors.json")
    w.add_argument("directory", type=Path)
    w.add_argument("--profile", choices=bench.ht.PROFILES, default="sim")
    w.add_argument("--seed", type=int, default=1)
    if size_help:
        w.add_argument("--size", type=int, help=size_help)
    w.add_argument("--test", action="append", help="only this test case and those it builds on")
    c = sub.add_parser("check", help="check the records of the bench, write report.txt")
    c.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "write":
        write(bench, args.directory, args.profile, args.seed, args.test, getattr(args, "size", None))
        return 0
    return check(bench, args.directory)
