# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of tools/check_pins.py --offline: a temporary repository with a system file, the submodule of Open Logic FT
with an annotated tag and a core verified with that tag. Only file transports are allowed, so that a call of the
published repositories fails instead of reaching the network."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
OLO_URL = "https://github.com/open-space-hdl/open-logic-ft"
CORE_URL = "https://github.com/open-space-hdl/opencore"
TAG = "4.7.0-ft.6"
ENV = dict(os.environ, GIT_ALLOW_PROTOCOL="file", GIT_TERMINAL_PROMPT="0")


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
                            cwd=cwd, env=ENV, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def init(repo: Path) -> None:
    repo.mkdir(parents=True)
    git(repo, "init", "-q")


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name + "\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def gitlink(repo: Path, path: str, sha: str) -> None:
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},{path}")


def source(sid: str, url: str, sha: str, **extra: str) -> dict:
    return {"id": sid, "name": sid, "url": url, "commit": sha, "library": sid, "fileList": "compile_order.txt",
            "fileListFormat": "files", **extra}


def make_repository(root: Path, later_pin: bool) -> tuple[str, str]:
    """Builds the repository; the system pins the tagged commit of Open Logic FT, or with later_pin a later commit.
    Returns the tagged and the later commit."""
    init(root)
    shutil.copytree(TOOLS, root / "tools", ignore=shutil.ignore_patterns("tests", "__pycache__", "vivado"))

    olo = root / "cores" / "open-logic-ft"
    init(olo)
    tagged = commit(olo, "a.vhd")
    git(olo, "tag", "-a", TAG, "-m", TAG)
    later = commit(olo, "b.vhd")
    pin = later if later_pin else tagged
    git(olo, "checkout", "-q", pin)

    core = root / "cores" / "opencore"
    init(core)
    commit(core, "core.vhd")
    gitlink(core, "open-logic", tagged)
    git(core, "commit", "-q", "-m", "open-logic")
    core_sha = git(core, "rev-parse", "HEAD")

    (root / ".gitmodules").write_text(
        f'[submodule "cores/open-logic-ft"]\n\tpath = cores/open-logic-ft\n\turl = {OLO_URL}\n'
        f'[submodule "cores/opencore"]\n\tpath = cores/opencore\n\turl = {CORE_URL}\n', encoding="utf-8")
    system = root / "systems" / "demo"
    system.mkdir(parents=True)
    data = {"format": "open-space-composer-system", "system": {"name": "demo"},
            "sources": [source("opencore", CORE_URL, core_sha, verifiedWith=TAG),
                        source("olo", OLO_URL, pin, tag=TAG)]}
    (system / "demo.composer.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    git(root, "add", ".gitmodules", "systems")
    gitlink(root, "cores/open-logic-ft", pin)
    gitlink(root, "cores/opencore", core_sha)
    return tagged, later


def check_pins(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "tools/check_pins.py", *args], cwd=root, env=ENV, capture_output=True,
                          text=True, check=False)


def test_offline_reads_the_peeled_tag_of_the_submodule(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    tagged, _ = make_repository(root, later_pin=False)
    olo = root / "cores" / "open-logic-ft"
    assert git(olo, "rev-parse", TAG) != tagged  # annotated: the tag object is not the commit
    result = check_pins(root, "--offline")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Pins OK: 1 system(s), 2 submodule(s)" in result.stdout


def test_without_offline_the_published_repository_is_needed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_repository(root, later_pin=False)
    result = check_pins(root)
    assert result.returncode != 0
    assert "use --offline" in result.stdout


def test_offline_fails_when_the_tag_is_not_the_pin(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    tagged, later = make_repository(root, later_pin=True)
    result = check_pins(root, "--offline")
    assert result.returncode != 0
    assert f"tag {TAG} of cores/open-logic-ft is {tagged}, the file pins {later}" in result.stdout
