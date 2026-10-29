# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Step log of a hardware test run: what the procedure does and finds, event by event.

Every event has the time, the test case, its number within the test case, a kind, a text, a result (pass, fail or
none) and its values. The sinks of a log receive every event: the pytest runner writes them as JSON lines into
steps.jsonl and as text into run.log; every event also goes to the logger "refsys" (INFO for the events that confirm
something, DEBUG for the register accesses and the passing checks), which pytest shows live on the console.

Kinds:

- step: a step of a test case, as the table of the procedure names it, with the requirements it confirms;
- read, write, wait: a register access over RMAP, with the register name when the address map knows it;
- expect: a value with the value expected (a register, a counter, a quantity), passed or failed;
- record: a recorded value that is no pass criterion;
- config: the configuration of a compressor (generics, runtime values, a COMMIT and its result);
- image: one image through a channel (samples, SDUs, compressed size, ratio, chunks, model and decoder results);
- packets: the Space Packets of an exchange per APID (counts, lengths, decryption);
- env: the environment of the run (versions, commits, the state of the working tree);
- result: the result of a test case (pass, fail, not run) and its duration.

The log observes only: it makes no request of its own, so a run with the log makes the requests of a run without it.
"""

import json
import logging
import time
from collections.abc import Callable

LOGGER = logging.getLogger("refsys")
LOGGER.addHandler(logging.NullHandler())   # no output of its own outside pytest (no last-resort handler)

# Kinds whose events go to the console at INFO; the others at DEBUG (the passing checks as well)
INFO_KINDS = {"step", "expect", "record", "config", "image", "packets", "env", "result"}


def now() -> str:
    """Local time with milliseconds, ISO 8601."""
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


def text_of(event: dict) -> str:
    """The event as one line of text: test case, number, kind, result and text."""
    result = f" {event['result'].upper()}" if event.get("result") else ""
    return f"{event['test'] or '-'} #{event['n']} {event['kind']}{result}: {event['text']}"


class StepLog:
    """The events of a run. begin() starts the numbering of a test case; every helper returns the event it made."""

    def __init__(self) -> None:
        self.test = ""
        self.n = 0
        self.sinks: list[Callable[[dict], None]] = []
        self.failed = 0

    def begin(self, test: str) -> None:
        self.test = test
        self.n = 0

    def emit(self, kind: str, text: str, result: str | None = None, level: int | None = None, **data) -> dict:
        self.n += 1
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

    def check(self, ok: bool, message: str) -> dict:
        """A check of the procedure (systest.check): its message describes the failure, so a passing check goes to the
        console only at DEBUG."""
        return self.emit("expect", message, "pass" if ok else "fail", logging.DEBUG if ok else None)

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
    """Sink that writes every event as one line of text with its time."""

    def __init__(self, path) -> None:
        self.file = open(path, "w", encoding="utf-8", newline="\n")  # noqa: SIM115 - closed by close()

    def __call__(self, event: dict) -> None:
        self.file.write(f"{event['time']} {text_of(event)}\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


NULL = StepLog()
