# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""--check-environment of the hardware test of every system, each in a fresh Python process, so that no other test
has imported a module of the system or the tools of OpenCompact before it. The check lists the items that a run of
the system needs, and its lines are those of the procedure of the system (targets/vck190/docs/hardware_test.md,
section 8) word for word. For --target model it needs no far-end software."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from refsys import systest, transport

ROOT = Path(__file__).resolve().parents[2]
SYSTEMS = ("spfi_compress", "spfi_compress_encrypt", "spfi_encrypt")
FAR_END = "Python package of the far-end software"


def far_end_installed() -> bool:
    try:
        transport.far_end_software()
    except ImportError:
        return False
    return True


def check(system: str, *options: str) -> subprocess.CompletedProcess:
    """hardware_test.py --check-environment of the system in a new Python process, from the root of the repository."""
    return subprocess.run([sys.executable, "-B", str(ROOT / "systems" / system / "sw" / "hardware_test.py"),
                           "--check-environment", *options], cwd=ROOT, capture_output=True, text=True, timeout=300,
                          check=False)


def printed(output: str) -> list[tuple[str, str, str]]:
    """Status (OK, FAIL, SKIP), item and value of every line of the check."""
    return [(line[:4].strip(), *line[5:].split(": ", 1)) for line in output.splitlines()]


def procedure(system: str) -> str:
    return (ROOT / "systems" / system / "targets" / "vck190" / "docs" / "hardware_test.md").read_text(encoding="utf-8")


def documented(system: str) -> list[tuple[str, str | None]]:
    """The lines that the procedure of the system shows for a test computer with everything installed: item and value
    (None for a placeholder such as <version>)."""
    block = re.search(r"```text\n((?:  OK   .*\n)+)  ```", procedure(system))
    assert block, f"the procedure of {system} shows no lines of --check-environment"
    lines = printed("\n".join(line[2:] for line in block.group(1).splitlines()))
    return [(item, None if re.fullmatch(r"<[^>]+>", value) else value) for _, item, value in lines]


@pytest.mark.parametrize("system", SYSTEMS)
def test_the_check_for_the_device_prints_the_lines_of_the_procedure(system):
    result = check(system)
    lines = printed(result.stdout)
    expected = documented(system)
    assert [item for _, item, _ in lines] == [item for item, _ in expected], result.stdout + result.stderr
    for (status, item, value), (_, documented_value) in zip(lines, expected):
        if item == FAR_END and not far_end_installed():
            assert (status, value) == ("FAIL", "not installed")
        else:
            assert status == "OK" and documented_value in (None, value), f"{status} {item}: {value}"
    assert result.returncode == (0 if far_end_installed() else 1)


@pytest.mark.parametrize("system", SYSTEMS)
def test_the_check_for_the_model_needs_no_far_end_software(system):
    result = check(system, "--target", "model")
    lines = printed(result.stdout)
    expected = documented(system)
    assert [item for _, item, _ in lines] == [item for item, _ in expected], result.stdout + result.stderr
    assert all(status == "OK" for status, _, _ in lines[:-1]), result.stdout
    assert lines[-1] == ("SKIP", FAR_END, "not needed for --target model")
    assert f"`SKIP {FAR_END}: not needed for --target model`" in procedure(system)
    assert result.returncode == 0


def test_target_goes_with_the_check_only():
    result = subprocess.run([sys.executable, "-B", str(ROOT / "systems" / "spfi_encrypt" / "sw" / "hardware_test.py"),
                             "--model", "--target", "model"], cwd=ROOT, capture_output=True, text=True, timeout=300,
                            check=False)
    assert result.returncode == 2 and "--target goes with --check-environment" in result.stderr


def test_a_missing_pycryptodome_is_reported(monkeypatch, capsys):
    for name in ("Crypto", "Crypto.Cipher", "Crypto.Cipher.AES"):
        monkeypatch.setitem(sys.modules, name, None)
    assert systest.check_environment("model", compressor=False, cipher=True) == 1
    assert "FAIL pycryptodome:" in capsys.readouterr().out
