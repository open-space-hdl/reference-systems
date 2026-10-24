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

import pytest


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


# --- a core verified with a newer Open Logic FT tag than the system's -------------------------------------------------
SYSTEM_TAG = "4.7.0-ft.8"
NEWER_TAG = "4.7.0-ft.9"
OLO_FILES = {
    # olo_a names olo_b of Open Logic (work.olo_b); the core names olo_a; olo_c is not used
    "src/base/vhdl/olo_a.vhd": "library work;\n    use work.olo_b.all;\n\npackage olo_a is\nend package;\n",
    "src/base/vhdl/olo_b.vhd": "package olo_b is\nend package;\n",
    "src/base/vhdl/olo_c.vhd": "entity olo_c is\nend entity;\n",
}
CORE_VHD = "library olo;\n    use olo.olo_a.all;\n\nentity core is\nend entity;\n"


def write(repo: Path, path: str, text: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")
    git(repo, "add", path)


def make_newer_repository(root: Path, change: str, bundle_tags: tuple[str, ...] | None = None) -> None:
    """Builds the repository: the system pins Open Logic FT at SYSTEM_TAG, its core was verified with NEWER_TAG, and
    the file `change` of Open Logic differs between the two tags. cores/open-logic-ft is a clone of the published
    repository, or with bundle_tags a clone of a bundle with those tags, as tools/make_offline_bundle.py writes it."""
    init(root)
    shutil.copytree(TOOLS, root / "tools", ignore=shutil.ignore_patterns("tests", "__pycache__", "vivado"))

    origin = root.parent / "olo_origin"
    init(origin)
    for path, text in OLO_FILES.items():
        write(origin, path, text)
    git(origin, "commit", "-q", "-m", "units")
    old = git(origin, "rev-parse", "HEAD")
    git(origin, "tag", "-a", SYSTEM_TAG, "-m", SYSTEM_TAG)
    write(origin, change, OLO_FILES[change] + "-- changed\n")
    git(origin, "commit", "-q", "-m", f"change {change}")
    new = git(origin, "rev-parse", "HEAD")
    git(origin, "tag", "-a", NEWER_TAG, "-m", NEWER_TAG)
    olo = root / "cores" / "open-logic-ft"
    olo.parent.mkdir(parents=True)
    if bundle_tags is None:
        git(root.parent, "clone", "-q", str(origin), str(olo))
    else:
        git(origin, "checkout", "-q", old)
        bundle = root.parent / "open-logic-ft.bundle"
        git(origin, "bundle", "create", str(bundle), "HEAD", *[f"refs/tags/{t}" for t in bundle_tags])
        git(root.parent, "clone", "-q", str(bundle), str(olo))
    git(olo, "checkout", "-q", old)

    core = root / "cores" / "opencore"
    init(core)
    write(core, "core.vhd", CORE_VHD)
    gitlink(core, "open-logic", new)
    git(core, "commit", "-q", "-m", "core")
    core_sha = git(core, "rev-parse", "HEAD")

    (root / ".gitmodules").write_text(
        f'[submodule "cores/open-logic-ft"]\n\tpath = cores/open-logic-ft\n\turl = {OLO_URL}\n'
        f'[submodule "cores/opencore"]\n\tpath = cores/opencore\n\turl = {CORE_URL}\n', encoding="utf-8")
    system = root / "systems" / "demo"
    (system / "hdl").mkdir(parents=True)
    data = {"format": "open-space-composer-system", "system": {"name": "demo"},
            "sources": [source("opencore", CORE_URL, core_sha, verifiedWith=NEWER_TAG),
                        source("olo", OLO_URL, old, tag=SYSTEM_TAG)]}
    (system / "demo.composer.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    order = [f"source opencore {CORE_URL} {core_sha}", f"source olo {OLO_URL} {old}"]
    order += [f"file olo open-logic-ft/{p}" for p in OLO_FILES] + ["file opencore opencore/core.vhd"]
    (system / "hdl" / "demo_compile_order.txt").write_text("\n".join(order) + "\n", encoding="utf-8")
    git(root, "add", ".gitmodules", "systems")
    gitlink(root, "cores/open-logic-ft", old)
    gitlink(root, "cores/opencore", core_sha)


def test_a_newer_verification_passes_when_the_used_open_logic_files_are_the_same(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_newer_repository(root, change="src/base/vhdl/olo_c.vhd")
    result = check_pins(root, "--offline")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (f"systems/demo/demo.composer.json: opencore: verified with {NEWER_TAG}, system olo {SYSTEM_TAG}, "
            "2 used Open Logic files identical") in result.stdout


def test_a_newer_verification_fails_when_a_used_open_logic_file_differs(tmp_path: Path) -> None:
    # olo_b is used through olo_a: the references of Open Logic to itself count
    root = tmp_path / "repo"
    make_newer_repository(root, change="src/base/vhdl/olo_b.vhd")
    result = check_pins(root, "--offline")
    assert result.returncode == 1
    assert (f"opencore was verified with {NEWER_TAG}, newer than {SYSTEM_TAG}, and 1 of the 2 Open Logic files it "
            "uses differ: src/base/vhdl/olo_b.vhd") in result.stdout


def test_a_newer_verification_without_its_tag_is_named(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_newer_repository(root, change="src/base/vhdl/olo_c.vhd")
    git(root / "cores" / "open-logic-ft", "tag", "-d", NEWER_TAG)
    result = check_pins(root, "--offline")
    assert result.returncode == 1
    assert f"opencore verifiedWith {NEWER_TAG} is not a tag of cores/open-logic-ft" in result.stdout


def test_a_bundle_clone_with_the_tags_of_the_offline_package_passes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_newer_repository(root, change="src/base/vhdl/olo_c.vhd", bundle_tags=(SYSTEM_TAG, NEWER_TAG))
    result = check_pins(root, "--offline")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 used Open Logic files identical" in result.stdout


def test_a_bundle_clone_without_the_verification_tag_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_newer_repository(root, change="src/base/vhdl/olo_c.vhd", bundle_tags=(SYSTEM_TAG,))
    result = check_pins(root, "--offline")
    assert result.returncode == 1
    assert f"opencore verifiedWith {NEWER_TAG} is not a tag of cores/open-logic-ft" in result.stdout


# --- the case of the systems: OpenFibre v0.1.3 verified with 4.7.0-ft.9, the systems at 4.7.0-ft.8 ----------------
def real_case(repo_id: str) -> tuple[list[str], list[str]]:
    """The Open Logic files that a core of spfi_encrypt uses and those that differ between 4.7.0-ft.8 and
    4.7.0-ft.9, from the submodules of this repository (skipped where a tag is not in cores/open-logic-ft)."""
    sys.path.insert(0, str(TOOLS))
    import check_pins as cp
    from composer_system import CORES, ROOT, load, sources

    olo = CORES / "open-logic-ft"
    tags = cp.local_tags(olo) if (olo / ".git").exists() else {}
    if SYSTEM_TAG not in tags or NEWER_TAG not in tags or not cp.has_commit(olo, tags[NEWER_TAG]):
        pytest.skip(f"cores/open-logic-ft has no {SYSTEM_TAG} and {NEWER_TAG}: git -C cores/open-logic-ft fetch --tags")
    system_file = ROOT / "systems" / "spfi_encrypt" / "spfi_encrypt.composer.json"
    src = next(s for s in sources(load(system_file)) if s.id == repo_id)
    return cp.compare_used_olo_files(system_file, src, olo, tags[SYSTEM_TAG], tags[NEWER_TAG])


def test_openfibre_uses_open_logic_files_that_are_the_same_in_ft8_and_ft9() -> None:
    used, differ = real_case("openfibre")
    assert len(used) == 35 and differ == []


def test_openrmap_uses_open_logic_files_that_differ_between_ft8_and_ft9() -> None:
    used, differ = real_case("openrmap")
    assert "src/axi/vhdl/olo_axi_master_full.vhd" in differ and set(differ) <= set(used)
