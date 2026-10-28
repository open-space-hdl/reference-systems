# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""The software of the hardware tests needs on the test computer Python 3 and the far-end software only: the
reference data come from the reference model of OpenCompact and the committed golden files, never from an external
program. These tests check the sources: no module of sw/refsys and of the software of the systems starts an external
program, and the hardware test of spfi_compress with its system model imports, at module level, only the standard
library, the modules of the repository and the tools of OpenCompact; the Python package of the far-end software is
imported only inside sw/refsys/transport.py, and pycryptodome only where a system with OpenCrypto needs it."""

import ast
import importlib.abc
import sys
from pathlib import Path

import pytest

from refsys import systest

ROOT = Path(__file__).resolve().parents[2]
REFSYS = ROOT / "sw" / "refsys"
TOOLS = ROOT / "cores" / "opencompact" / "tools"
SOURCES = sorted(REFSYS.glob("*.py")) + sorted(ROOT.glob("systems/*/sw/*.py"))
# modules that start other programs, and the functions of os that do
PROGRAM_MODULES = {"subprocess", "multiprocessing", "pty", "asyncio"}
OS_FUNCTIONS = ("system", "popen", "startfile", "exec", "spawn", "posix_spawn", "fork")
# the far-end software, imported by sw/refsys/transport.py only
FAR_END = "STAR_system"


def external_programs(source: str) -> list[str]:
    """The places of a Python source that start an external program or import a module that does."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"line {node.lineno}: import {a.name}" for a in node.names
                      if a.name.split(".")[0] in PROGRAM_MODULES]
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module in PROGRAM_MODULES:
                found.append(f"line {node.lineno}: from {node.module} import")
            elif module == "os":
                found += [f"line {node.lineno}: from os import {a.name}" for a in node.names
                          if a.name.startswith(OS_FUNCTIONS)]
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os"
              and node.attr.startswith(OS_FUNCTIONS)):
            found.append(f"line {node.lineno}: os.{node.attr}")
    return found


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_module_starts_an_external_program(path):
    assert external_programs(path.read_text(encoding="utf-8")) == []


def test_the_check_finds_an_external_program():
    assert external_programs("import subprocess\nsubprocess.run(['git'])\n")
    assert external_programs("import os\nos.system('vivado')\n")
    assert external_programs("from os import popen\n")
    assert external_programs("def f():\n    from subprocess import run\n")
    assert external_programs("import os\nos.environ['A'] = '1'\n") == []


def module_imports(path: Path) -> list[tuple[str, int]]:
    """The modules that a source imports when it is imported (module level, also inside if and try; not inside a
    function or class), with their lines: (name, level of a relative import)."""
    names = []

    def visit(body):
        for node in body:
            if isinstance(node, ast.Import):
                names.extend((a.name, 0) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.append((node.module or "", node.level))
            elif isinstance(node, (ast.If, ast.Try)):
                for block in (node.body, getattr(node, "orelse", []), getattr(node, "finalbody", []),
                              *[h.body for h in getattr(node, "handlers", [])]):
                    visit(block)

    visit(ast.parse(path.read_text(encoding="utf-8")).body)
    return names


def first_party(name: str, here: Path) -> Path | None:
    """The source of a module of the repository or of the tools of OpenCompact (None for another module)."""
    top, _, rest = name.partition(".")
    if top == "refsys":
        return REFSYS / f"{rest or '__init__'}.py"
    for directory in (here, TOOLS):
        if (directory / f"{top}.py").exists():
            return directory / f"{top}.py"
    return None


def import_closure(roots: list[Path]) -> set[str]:
    """The modules outside the repository and the tools of OpenCompact that importing roots imports."""
    seen, todo, outside = set(), list(roots), set()
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        for name, level in module_imports(path):
            if level:
                continue
            source = first_party(name, path.parent)
            if source is None:
                outside.add(name.split(".")[0])
            elif source.exists():
                todo.append(source)
    return outside


SPFI_COMPRESS = [ROOT / "systems" / "spfi_compress" / "sw" / name for name in ("hardware_test.py", "system_model.py")]


def test_spfi_compress_imports_the_standard_library_only():
    outside = import_closure(SPFI_COMPRESS)
    assert outside - set(sys.stdlib_module_names) == set()


def test_the_closure_check_finds_a_third_party_import(tmp_path):
    source = tmp_path / "probe.py"
    source.write_text("import json\nimport numpy\nfrom refsys import ocp\n", encoding="utf-8")
    assert import_closure([source]) - set(sys.stdlib_module_names) == {"numpy"}


def test_the_far_end_software_is_imported_by_the_transport_only():
    users = [p for p in SOURCES if FAR_END in {n.split(".")[0] for n, _ in all_imports(p)}]
    assert users == [REFSYS / "transport.py"]


def all_imports(path: Path) -> list[tuple[str, int]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [(a.name, 0) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "", node.level))
    return out


class ThirdPartyBlocker(importlib.abc.MetaPathFinder):
    """Refuses every import of a module that is neither in the standard library nor of the repository or the tools of
    OpenCompact."""

    def __init__(self):
        self.refused = []

    def find_spec(self, fullname, path, target=None):
        top = fullname.split(".")[0]
        if top in sys.stdlib_module_names or first_party(top, SPFI_COMPRESS[0].parent) is not None:
            return None
        self.refused.append(fullname)
        raise ImportError(f"third-party module {fullname} refused")


def test_spfi_compress_runs_against_its_model_without_third_party_packages():
    """The hardware test of spfi_compress runs a test case against its system model with every third-party import
    refused: also an import inside a function on its path fails it (pycryptodome, for example)."""
    blocker = ThirdPartyBlocker()
    names = [n for n in sys.modules if n.split(".")[0] in ("refsys", "Crypto")
             or first_party(n.split(".")[0], SPFI_COMPRESS[0].parent)]
    saved = {n: sys.modules.pop(n) for n in names}
    sys.meta_path.insert(0, blocker)
    try:
        from refsys import systest as fresh

        ht, sm = fresh.system_modules(SPFI_COMPRESS[0].parent, "hardware_test", "system_model")
        system = ht.System(dims=(8, 4, 3), chunk_bytes=256)
        ctx = ht.make_context(1, ht.PROFILES["sim"], system=system, sdu_bytes=200)
        model = sm.SystemModel(ht.Setup(), system=system)
        results = ht.run(model, ctx, ht.select_tests(["TC-RS-HW-06", "TC-RS-HW-10"]))
    finally:
        sys.meta_path.remove(blocker)
        for n in [n for n in sys.modules if n.split(".")[0] in ("refsys", "Crypto")
                  or first_party(n.split(".")[0], SPFI_COMPRESS[0].parent)]:
            del sys.modules[n]
        sys.modules.update(saved)
    assert blocker.refused == [] and all(error is None for _, error in results), (blocker.refused, results)


def test_the_blocker_refuses_a_third_party_import():
    blocker = ThirdPartyBlocker()
    sys.meta_path.insert(0, blocker)
    try:
        with pytest.raises(ImportError):
            __import__("some_third_party_package_for_the_test")
    finally:
        sys.meta_path.remove(blocker)
    assert blocker.refused == ["some_third_party_package_for_the_test"]


def test_check_environment_reports_a_missing_far_end_package(monkeypatch, capsys):
    from refsys import transport

    def missing():
        raise ImportError("No module named 'far_end'")

    monkeypatch.setattr(transport, "far_end_software", missing)
    assert systest.check_environment() == 1
    out = capsys.readouterr().out
    assert "FAIL Python package of the far-end software" in out and "OK   Python" in out
    monkeypatch.setattr(transport, "far_end_software", lambda: "found")
    assert systest.check_environment() == 0


def test_device_model_and_check_environment_exclude_each_other():
    parser = systest.arguments("test", model=True)
    assert parser.parse_args(["--model"]).model
    assert parser.parse_args(["--device", "d"]).device == "d"
    for argv in ([], ["--model", "--device", "d"], ["--check-environment", "--model"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
