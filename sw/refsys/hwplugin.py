# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""pytest plugin of the hardware tests of the reference systems (loaded by conftest.py at the root of the repository).

The test cases stay the functions TC-RS-HW-nn of systems/<system>/sw/hardware_test.py; the module
systems/<system>/sw/hwtest/test_<system>.py turns them into one pytest test each, in the order of TESTS, with the
markers tc, requirement and operator. This plugin adds the options, opens the far end (the device through the
software of the test equipment, or the system model) once per system, runs every test case through
systest.run_one with the step log of the run, and writes the outputs into a new directory below --results:

- steps.jsonl: every event of the step log (refsys/steplog.py), one JSON object per line;
- run.log: the same as text;
- junit.xml: the JUnit XML report of pytest, with the requirements of every test case as properties;
- records_<system>_<time>.json and report_<system>.md: the records of the run (results, recorded values, compressed
  images, requirements) and their tables (tools/render_records.py).

Nothing is written into the repository: --results must lie outside it, the cache provider is off (pytest.ini), and
the run records `git status --porcelain --ignored` of the repository and of every submodule at its start and at its
end. Python writes its bytecode next to the sources unless it runs with -B; the run warns when it does.

The run stops at the first failure: the later test cases build on the earlier ones and are reported as not run.
--keep-going runs a later test case when the test cases it builds on passed (diagnosis only; the report says so).
--tc selects test cases together with those they build on; -k is refused, since it would drop them.
"""

import importlib.metadata
import json
import logging
import platform
import subprocess
import sys
import time
from pathlib import Path

import pytest

from refsys import steplog, systest

ROOT = systest.ROOT
HWTEST = "hwtest"
LOG_KEY = pytest.StashKey[steplog.StepLog]()
RUN_KEY = pytest.StashKey[Path]()
SINKS_KEY = pytest.StashKey[list]()
STATE_KEY = pytest.StashKey[dict]()


# --- options and configuration ---------------------------------------------------------------------------------------
def pytest_addoption(parser: pytest.Parser) -> None:
    g = parser.getgroup("hardware test", "hardware test of the reference systems (systems/<system>/sw/hwtest)")
    g.addoption("--target", choices=("device", "model"),
                help="run the hardware tests against the device (the test equipment) or the system model")
    g.addoption("--device", help="name of the STAR-Ultra device in STAR-System (with --target device)")
    g.addoption("--profile", choices=("lab", "sim"), default="lab", help="size of the run (default lab)")
    g.addoption("--seed", type=int, default=1, help="seed of the images, keys and packets (default 1)")
    g.addoption("--results", help="directory for the outputs of the run, outside the repository (required)")
    g.addoption("--tc", action="append", default=[],
                help="run only this test case and those it builds on (repeatable), for example TC-RS-HW-08")
    g.addoption("--keep-going", action="store_true",
                help="after a failure, run the test cases whose own dependencies passed (diagnosis only)")


def hardware(config: pytest.Config) -> bool:
    return config.getoption("target") is not None


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """The hardware tests are collected only with --target: a plain pytest run is the unit tests."""
    if HWTEST in collection_path.parts and not hardware(config):
        return True
    return None


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    if not hardware(config):
        return
    if config.getoption("target") == "device" and not config.getoption("device"):
        raise pytest.UsageError("--target device needs --device <name of the STAR-Ultra in STAR-System>")
    if not config.getoption("results"):
        raise pytest.UsageError("--results <directory outside the repository> is required with --target")
    results = Path(config.getoption("results")).resolve()
    if inside(results, ROOT.resolve()):
        raise pytest.UsageError(f"--results {results} lies inside the repository; choose a directory outside it")
    if config.option.keyword:
        raise pytest.UsageError("-k is not used with the hardware tests: --tc selects test cases together with the "
                                "test cases they build on")
    run = results / time.strftime("%Y%m%d_%H%M%S")
    k = 1
    while run.exists():
        k += 1
        run = results / f"{time.strftime('%Y%m%d_%H%M%S')}_{k}"
    run.mkdir(parents=True)
    config.stash[RUN_KEY] = run
    # the events at INFO live on the console (only for the hardware tests: the unit tests run the procedures quietly)
    if config.option.log_cli_level is None:
        config.option.log_cli_level = "INFO"
    config.option.log_cli_format = config.option.log_cli_format or "%(asctime)s %(levelname)-7s %(message)s"
    config.option.log_cli_date_format = config.option.log_cli_date_format or "%H:%M:%S"
    if not config.option.xmlpath:
        config.option.xmlpath = str(run / "junit.xml")
    log = steplog.StepLog()
    sinks = [steplog.JsonLines(run / "steps.jsonl"), steplog.TextLines(run / "run.log")]
    log.sinks += sinks
    config.stash[LOG_KEY] = log
    config.stash[SINKS_KEY] = sinks
    config.stash[STATE_KEY] = {"failed": None, "outcomes": {}, "keep_going": config.getoption("keep_going")}
    SESSION["log"] = log
    log.begin("")
    log.emit("env", f"results in {run}", results=str(run))
    if not sys.flags.dont_write_bytecode:
        log.emit("env", "Python writes bytecode (__pycache__) into the repository: run pytest as python -B -m pytest",
                 level=logging.WARNING, bytecode=True)


def pytest_unconfigure(config: pytest.Config) -> None:
    SESSION.pop("log", None)
    for sink in config.stash.get(SINKS_KEY, []):
        sink.close()


def pytest_report_header(config: pytest.Config) -> list[str]:
    if not hardware(config):
        return []
    return [f"hardware test: target {config.getoption('target')}, profile {config.getoption('profile')}, seed "
            f"{config.getoption('seed')}, results {config.stash[RUN_KEY]}"]


# --- test cases as pytest parameters ---------------------------------------------------------------------------------
def parameters(ht) -> list:
    """One pytest parameter per test case of a hardware test module, in the order of TESTS, with its markers: tc (name
    and title), requirement (the IDs of REQUIREMENTS) and operator (a step of the operator at the far end)."""
    out = []
    requirements = getattr(ht, "REQUIREMENTS", {})
    operator = getattr(ht, "OPERATOR_TESTS", ())
    for test in ht.TESTS:
        name = systest.test_name(test)
        marks = [pytest.mark.tc(name), pytest.mark.requirement(*requirements.get(name, ()))]
        if name in operator:
            marks.append(pytest.mark.operator)
        out.append(pytest.param(test, id=name, marks=marks))
    return out


def tc_of(item: pytest.Item) -> str | None:
    marker = item.get_closest_marker("tc")
    return marker.args[0] if marker else None


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keeps the test cases that --tc selects with those they build on; the order stays that of TESTS."""
    if not hardware(config):
        return
    keep, drop = [], []
    selected_by_module: dict = {}
    for item in items:
        name = tc_of(item)
        if name is None:
            keep.append(item)
            continue
        ht = item.module.ht
        if item.module not in selected_by_module:
            try:
                tests = ht.select_tests(config.getoption("tc") or None)
            except ValueError as err:
                raise pytest.UsageError(str(err)) from err
            selected_by_module[item.module] = [systest.test_name(t) for t in tests]
        (keep if name in selected_by_module[item.module] else drop).append(item)
    for module, names in selected_by_module.items():
        order = [tc_of(i) for i in keep if getattr(i, "module", None) is module and tc_of(i)]
        if order != names:
            raise pytest.UsageError(f"the hardware tests of {module.__name__} run in the order of TESTS: {names}")
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep


@pytest.fixture(autouse=True)
def _requirements(request: pytest.FixtureRequest) -> None:
    """The requirements of a test case as a property of its JUnit XML test case."""
    marker = request.node.get_closest_marker("requirement")
    if marker and marker.args:
        request.node.user_properties.append(("requirement", " ".join(marker.args)))


# --- the run of one system -------------------------------------------------------------------------------------------
class HardwareRun:
    """The far end, the context and the register access of the hardware test of one system, for one pytest
    session; run() runs one test case."""

    def __init__(self, config: pytest.Config, module) -> None:
        self.config = config
        self.module = module
        self.ht = module.ht
        self.system = module.SYSTEM
        self.log = config.stash[LOG_KEY]
        self.state = config.stash[STATE_KEY]
        self.target = config.getoption("target")
        self.profile = config.getoption("profile")
        self.started = time.strftime("%Y-%m-%d %H:%M:%S")
        self.results: list[tuple[str, str | None]] = []
        self.not_run: list[tuple[str, str]] = []
        self.log.begin("")
        environment(self.log, "start")
        self.ctx = module.context(config.getoption("seed"), self.profile)
        self.ctx.log = self.log
        self.ctx.requirements = getattr(self.ht, "REQUIREMENTS", {})
        if self.target == "model":
            self.transport = module.model(self.ctx)
        else:
            self.transport = systest.open_device(config.getoption("device"), self.ctx)
        try:
            self.transport.operator = self.operator_step(self.transport.operator)
            tests = self.ht.select_tests(config.getoption("tc") or None)
            self.regs = systest.prepare(self.transport, self.ctx, tests)
        except BaseException:
            self.transport.close()
            raise
        self.token = systest.ACTIVE_LOG.set(self.log)

    def operator_step(self, step):
        """The operator step of the far end with the output and the input of pytest released (the operator reads the
        step on the console and confirms it with Enter)."""
        capture = self.config.pluginmanager.getplugin("capturemanager")

        def wrapped(name: str) -> None:
            self.log.emit("step", f"operator step at the far end: {name}", operator=name)
            if capture is None:
                return step(name)
            capture.suspend_global_capture(in_=True)
            try:
                return step(name)
            finally:
                capture.resume_global_capture()
        return wrapped

    def run(self, test) -> None:
        name = systest.test_name(test)
        failed = self.state["failed"]
        if failed and not self.state["keep_going"]:
            self.skip(name, f"not run: {failed} failed")
        needed = [systest.test_name(t) for t in getattr(self.ht, "REQUIRES", {}).get(test, [])]
        missing = [n for n in needed if self.state["outcomes"].get(n) != "passed"]
        if failed and missing:
            self.skip(name, f"not run: {', '.join(missing)} did not pass")
        try:
            systest.run_one(self.regs, self.ctx, test)
        except BaseException as err:
            self.results.append((name, str(err) or type(err).__name__))
            self.state["outcomes"][name] = "failed"
            self.state["failed"] = self.state["failed"] or name
            raise
        self.results.append((name, None))
        self.state["outcomes"][name] = "passed"

    def skip(self, name: str, reason: str) -> None:
        self.not_run.append((name, reason))
        self.state["outcomes"][name] = "not run"
        self.log.begin(name)
        self.log.emit("result", reason, "not run")
        pytest.skip(reason)

    def close(self) -> None:
        systest.ACTIVE_LOG.reset(self.token)
        self.log.begin("")
        environment(self.log, "end")
        if self.target == "device":
            self.transport.close()
        run = self.config.stash[RUN_KEY]
        extra = self.module.records_extra(self.ctx) if hasattr(self.module, "records_extra") else {}
        extra |= {"requirements": getattr(self.ht, "REQUIREMENTS", {}),
                  "requirements_note": getattr(self.ht, "REQUIREMENTS_NOTE", ""),
                  "not_run": [{"test": n, "reason": r} for n, r in self.not_run],
                  "keep_going": self.state["keep_going"]}
        path = systest.write_records(run, self.system, "device" if self.target == "device" else "system model",
                                     self.profile, self.ctx, self.results, self.started, extra)
        render = load_renderer()
        (run / f"report_{self.system}.md").write_text(render.render(json_of(path)), encoding="utf-8", newline="\n")


def json_of(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_renderer():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        return importlib.import_module("render_records")
    finally:
        sys.path.remove(str(ROOT / "tools"))


@pytest.fixture(scope="module")
def hw(request: pytest.FixtureRequest, record_testsuite_property):
    """The hardware test of the system of the test module (its SYSTEM, ht, context(), model())."""
    run = HardwareRun(request.config, request.module)
    for name, value in (("system", run.system), ("target", run.target), ("profile", run.profile),
                        ("seed", request.config.getoption("seed")),
                        ("commit", (git("rev-parse", "HEAD") or "").strip()),
                        ("keep_going", run.state["keep_going"])):
        record_testsuite_property(name, value)
    yield run
    run.close()


# --- results ---------------------------------------------------------------------------------------------------------
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """One result line per test case in the step log, with its duration."""
    tc = next((k for k in report.keywords if k.startswith("TC-RS-HW-")), None)
    if tc is None or not (report.when == "call" or (report.when == "setup" and not report.passed)):
        return
    log = SESSION.get("log")
    if log is None:
        return
    outcome = {"passed": "pass", "failed": "fail", "skipped": "not run"}[report.outcome]
    if log.test != tc:
        log.begin(tc)
    log.emit("result", f"{tc} {outcome.upper()} {report.duration:.1f} s", outcome, duration=report.duration)


# the step log of the hardware test session (pytest_runtest_logreport has no access to the configuration)
SESSION: dict = {}


def pytest_terminal_summary(terminalreporter, exitstatus: int, config: pytest.Config) -> None:
    if not hardware(config):
        return
    tr = terminalreporter
    rows = []
    for outcome in ("passed", "failed", "skipped"):
        for report in tr.stats.get(outcome, []):
            tc = next((k for k in report.keywords if k.startswith("TC-RS-HW-")), None)
            if tc and (report.when == "call" or outcome == "skipped"):
                reason = report.longrepr[2] if outcome == "skipped" and isinstance(report.longrepr, tuple) else ""
                rows.append((tc, {"passed": "Pass", "failed": "Fail", "skipped": "Not run"}[outcome],
                             report.duration, str(reason).removeprefix("Skipped: ")))
    rows.sort(key=lambda r: r[0])
    tr.section("hardware test")
    if config.stash[STATE_KEY]["keep_going"]:
        tr.write_line("--keep-going: test cases after a failure ran; this run is for diagnosis, not evidence")
    for tc, result, duration, reason in rows:
        tr.write_line(f"{tc:<13} {result:<8} {duration:8.1f} s  {reason}")
    tr.write_line(f"outputs: {config.stash[RUN_KEY]}")


# --- environment -----------------------------------------------------------------------------------------------------
def git(*args: str, cwd: Path = ROOT) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def tree_state() -> dict[str, list[str]] | None:
    """`git status --porcelain --ignored` of the repository and of every submodule (recursively), by path; None when
    git is missing or the repository is no git checkout (the state is then unknown)."""
    if (git("rev-parse", "--is-inside-work-tree") or "").strip() != "true":
        return None
    state = {".": (git("status", "--porcelain", "--ignored") or "").splitlines()}
    for line in (git("submodule", "status", "--recursive") or "").splitlines():
        path = line[1:].split()[1]
        state[path] = (git("status", "--porcelain", "--ignored", cwd=ROOT / path) or "").splitlines()
    return state


def environment(log: steplog.StepLog, when: str) -> None:
    """The environment of the run as env events: versions and commits at the start, the state of the working tree
    at the start and at the end."""
    if when == "start":
        versions = {"python": platform.python_version(), "pytest": pytest.__version__, "os": platform.platform()}
        for package in ("openspacehdl", "pycryptodome"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        commit = (git("rev-parse", "HEAD") or "").strip() or None
        submodules = (git("submodule", "status", "--recursive") or "").splitlines()
        log.emit("env", f"repository {commit}, Python {versions['python']}, pytest {versions['pytest']}",
                 versions=versions, commit=commit, submodules=submodules, bytecode=not sys.flags.dont_write_bytecode)
    state = tree_state()
    if state is None:
        log.emit("env", f"working tree at the {when} of the run: tree state unknown (git is missing or the "
                 "repository is no git checkout)", level=logging.WARNING, when=when, git_status=None,
                 git=git("--version") is not None)
        return
    changed = {path: lines for path, lines in state.items() if lines}
    entries = sum(len(v) for v in changed.values())
    text = f"working tree at the {when} of the run: " + (
        f"{entries} entries in {', '.join(changed)}" if changed else "clean (git status --porcelain --ignored empty)")
    log.emit("env", text, when=when, git_status=state, git=git("--version") is not None)
