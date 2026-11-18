# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Step log of a hardware test run: what the procedure does and finds, event by event.

Every event has the time, the test case, its number within the test case, a kind, a text, a result (pass, fail or
none) and its values. The sinks of a log receive every event: the pytest runner writes all of them as JSON lines into
steps.jsonl and those that a reader follows (without the register reads and writes, READABLE) as text into run.log;
every event also goes to the logger "refsys" (INFO for the events that confirm something, DEBUG for the register
accesses), which pytest shows live on the console.

Kinds:

- step: a step of a test case, as the table of the procedure names it, with the requirements it confirms;
- read, write: a register access over RMAP, with the register name when the address map knows it;
- wait: a register polled until it has a value, one event per wait with the polls and the time it took;
- expect: a value with the value expected (a register, a counter, a quantity), passed or failed;
- check: a pass criterion of the procedure (systest.check) that failed, with its text. A passing check writes no
  event: the result of the test case counts them;
- record: a recorded value that is no pass criterion;
- note: what the procedure tells the reader that is no value (a retry, packets discarded from a channel);
- snapshot: the state of the system read after a failure (link, lanes, error registers), for the diagnosis;
- config: the configuration of a compressor (generics, runtime values, a COMMIT and its result);
- image: one image through a channel (samples, SDUs, compressed size, ratio, chunks, model and decoder results);
- packets: the Space Packets of an exchange per APID (counts, lengths, decryption);
- env: the environment of the run (versions, commits, the state of the working tree);
- result: the result of a test case (pass, fail, not run), its duration and the number of its passing checks.

The log observes only: it makes no request of its own, so a run with the log makes the requests of a run without it.
"""

import json
import logging
import time
from collections.abc import Callable

LOGGER = logging.getLogger("refsys")
LOGGER.addHandler(logging.NullHandler())   # no output of its own outside pytest (no last-resort handler)

# Kinds whose events go to the console at INFO; the others at DEBUG
INFO_KINDS = {"step", "wait", "expect", "record", "note", "snapshot", "config", "image", "packets", "env", "result"}

# Kinds of run.log: every kind but the register reads and writes, which steps.jsonl keeps
READABLE = INFO_KINDS | {"check"}


def now() -> str:
    """Local time with milliseconds, ISO 8601."""
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


def text_of(event: dict) -> str:
    """The event as one line of text: test case, number, kind, result and text."""
    result = f" {event['result'].upper()}" if event.get("result") else ""
    return f"{event['test'] or '-'} #{event['n']} {event['kind']}{result}: {event['text']}"


class StepLog:
    """The events of a run. begin() sets the test case of the next events; the events of a test case (and those of
    the run outside a test case) are numbered from 1, across every begin() of it. Every helper returns the event it
    made. The passing checks of a test case are counted (passed), not written."""

    def __init__(self) -> None:
        self.test = ""
        self.n = 0
        self.counts: dict[str, int] = {}
        self.passed: dict[str, int] = {}
        self.sinks: list[Callable[[dict], None]] = []
        self.failed = 0

    def begin(self, test: str) -> None:
        self.test = test
        self.n = self.counts.get(test, 0)

    def passed_checks(self, test: str) -> int:
        """The passing checks of a test case, with those of the test cases that it runs again (TC-RS-HW-18, 23 log
        them as "<test> <name>")."""
        return sum(n for name, n in self.passed.items() if name == test or name.startswith(f"{test} "))

    def emit(self, kind: str, text: str, result: str | None = None, level: int | None = None, **data) -> dict:
        self.n += 1
        self.counts[self.test] = self.n
        event = {"time": now(), "test": self.test, "n": self.n, "kind": kind, "text": text, "result": result,
                 "data": data}
        self.failed += result == "fail"
        for sink in self.sinks:
            sink(event)
        if level is None:
            level = logging.INFO if kind in INFO_KINDS else logging.DEBUG
        LOGGER.log(logging.WARNING if result == "fail" else level, text_of(event))
        return event

    # --- helpers of the procedure -----------------------------------------------------------------------------------
    def step(self, text: str, requirements: tuple[str, ...] = ()) -> dict:
        return self.emit("step", text, requirements=list(requirements))

    def expect(self, what: str, actual, expected, ok: bool | None = None, quiet: bool = False, **data) -> dict:
        """A value and the value expected; ok defaults to actual == expected. quiet: a passing check that goes to the
        console only at DEBUG."""
        ok = actual == expected if ok is None else ok
        text = f"{what}: {show(actual)}, expected {show(expected)}"
        level = logging.DEBUG if quiet and ok else None
        return self.emit("expect", text, "pass" if ok else "fail", level, what=what, actual=actual, expected=expected,
                         **data)

    def check(self, ok: bool, message: str) -> dict | None:
        """A check of the procedure (systest.check), whose message is the text it fails with: a failing check is an
        event, a passing one is counted for its test case (passed) and makes none."""
        if ok:
            self.passed[self.test] = self.passed.get(self.test, 0) + 1
            return None
        return self.emit("check", message, "fail")

    def access(self, kind: str, address: int, words: list[int] | None = None, name: str = "", **data) -> dict:
        where = name or f"0x{address:08X}"
        values = " ".join(f"0x{w:08X}" for w in words or [])
        text = f"{where} {'=' if kind == 'read' else '<='} {values}" if words is not None else where
        return self.emit(kind, text, address=address, words=words, name=name, **data)


def show(value) -> str:
    """A value for the text of an event: integers above 9 in hexadecimal as well."""
    if isinstance(value, bool) or not isinstance(value, int):
        return str(value)
    return f"0x{value:08X} ({value})" if value > 9 else str(value)


class JsonLines:
    """Sink that writes every event as one JSON object per line."""

    def __init__(self, path) -> None:
        self.file = open(path, "w", encoding="utf-8", newline="\n")  # noqa: SIM115 - closed by close()

    def __call__(self, event: dict) -> None:
        self.file.write(json.dumps(event, default=str) + "\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


class TextLines:
    """Sink that writes the events of the given kinds (default: all) as one line of text each, with its time."""

    def __init__(self, path, kinds: set[str] | None = None) -> None:
        self.file = open(path, "w", encoding="utf-8", newline="\n")  # noqa: SIM115 - closed by close()
        self.kinds = kinds

    def __call__(self, event: dict) -> None:
        if self.kinds is not None and event["kind"] not in self.kinds:
            return
        self.file.write(f"{event['time']} {text_of(event)}\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


NULL = StepLog()
