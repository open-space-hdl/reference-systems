# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of the README.txt of tools/make_offline_bundle.py: its build commands are the command block of section 4
(Build) of the hardware test procedure, with the text in front of it, read from the repository at a commit."""

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import make_offline_bundle as mob  # noqa: E402

DOC = "systems/spfi_encrypt/targets/vck190/docs/hardware_test.md"
BUILD = "vivado -mode batch -source systems/spfi_encrypt/targets/vck190/tcl/build.tcl"


def readme(ref: str = "HEAD") -> str:
    """README.txt of a package of ref (HEAD: a CI checkout has no local branch)."""
    links = {p: sha for p, sha in mob.gitlinks(ref).items() if p.startswith("cores/")}
    return mob.readme(ref, mob.git("rev-parse", ref), links, {p: "" for p in links}, "0" * 40)


def section_4_commands(text: str) -> list[str]:
    """The lines of the first shell block after the heading of section 4."""
    lines = text.splitlines()
    start = lines.index("```shell", lines.index("## 4. Build"))
    return lines[start + 1:lines.index("```", start + 1)]


def test_readme_shows_both_build_commands_after_the_clock_check():
    text = readme()
    lines = text.splitlines()
    assert "    " + BUILD in lines
    assert "    " + BUILD + " -tclargs all si570" in lines
    flat = " ".join(text.split())
    assert "Confirm the reference clock on the board first (section 5, step 2)" in flat
    assert "build the image of that source only" in flat
    assert flat.index("Confirm the reference clock") < flat.index(BUILD)


def test_readme_build_commands_are_the_block_of_section_4():
    block = section_4_commands(mob.git("show", f"HEAD:{DOC}"))
    lines = readme().splitlines()
    # every system has its part; that of spfi_encrypt starts with its heading
    title = f"spfi_encrypt on the VCK190 ({DOC}, section 4)"
    heading = next(i for i, line in enumerate(lines) if line.startswith(title))
    start = lines.index("    " + block[0], heading)
    assert lines[start:start + len(block)] == ["    " + c for c in block]


def test_a_procedure_without_build_commands_stops_the_package():
    text = "## 4. Build\n\nText only.\n\n## 5. Board set-up\n\n```shell\nvivado\n```\n"
    with pytest.raises(SystemExit, match=r"section 4 \(Build\) has no shell command block"):
        mob.build_section(text, "doc")


def test_the_bundle_of_open_logic_carries_every_tag_that_the_system_files_name():
    tags = mob.olo_tags("HEAD")
    for path in Path(mob.ROOT / "systems").glob("*/*.composer.json"):
        data = json.loads(mob.git("show", f"HEAD:{path.relative_to(mob.ROOT).as_posix()}"))
        for source in data["sources"]:
            for tag in (source.get("tag"), source.get("verifiedWith")):
                assert tag is None or tag in tags
    assert all(tag.startswith("4.") and "-ft." in tag for tag in tags)
