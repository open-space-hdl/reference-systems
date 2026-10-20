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


def test_readme_shows_the_build_commands_of_every_clock_option_after_the_clock_check():
    text = readme()
    lines = text.splitlines()
    assert "    " + BUILD in lines
    assert "    " + BUILD + " -tclargs all si570 156.25" in lines
    assert "    " + BUILD + " -tclargs all 8a34001" in lines
    flat = " ".join(text.split())
    assert "Confirm the reference clock on the board first (section 5, step 2)" in flat
    assert "build the image of that option only" in flat
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


def bat(ref: str = "HEAD") -> str:
    """build.bat of a package of ref."""
    links = {p: sha for p, sha in mob.gitlinks(ref).items() if p.startswith("cores/")}
    return mob.build_bat("develop", mob.git("rev-parse", ref), links)


def test_build_bat_has_windows_line_ends_and_the_commit_of_the_package():
    text = bat()
    assert "\n" not in text.replace("\r\n", "")
    assert f'set "REF_SHA={mob.git("rev-parse", "HEAD")}"' in text.splitlines()


def test_build_bat_clones_every_bundle_as_readme_does():
    lines = bat().splitlines()
    assert 'git clone -b %REF% "%PKG%/reference-systems.bundle" "%RS_DIR%" || exit /b 1' in lines
    for p in (p for p in mob.gitlinks("HEAD") if p.startswith("cores/")):
        assert f'git config submodule.{p}.url "%PKG%/{Path(p).name}.bundle" || exit /b 1' in lines
    assert 'git -C cores/open-logic-ft config submodule.3rdParty/en_cl_fix.url "%PKG%/en_cl_fix.bundle" || exit /b 1' \
        in lines


def test_build_bat_knows_every_target_and_shows_the_options_of_section_4():
    lines = bat().splitlines()
    targets = mob.build_options("HEAD")
    for system, board, _ in targets:
        assert f'if /i "%SYSTEM%"=="{system}" set "BOARD={board}"' in lines
    first, _, options = targets[0]
    assert options[0][1] == ""   # the default image comes first, without words
    for comment, words in options:
        assert any(line.startswith(f"rem   build.bat {first} {words}".rstrip()) and line.endswith(comment)
                   for line in lines)
    assert any("systems/%SYSTEM%/targets/%BOARD%/tcl/build.tcl -tclargs%WORDS%" in line for line in lines)


def test_build_bat_sets_the_project_directory_and_readme_says_how_to_keep_a_project():
    assert 'if not defined RS_VIVADO_OUT set "RS_VIVADO_OUT=C:/git/rs"' in bat().splitlines()
    flat = " ".join(readme().split())
    assert "create_project -force, so it replaces the project in RS_VIVADO_OUT (default C:/git/rs)" in flat
    assert "set RS_VIVADO_OUT=C:/git/rs2" in flat


def test_the_package_options_set_the_directories_of_build_bat_and_readme():
    links = {p: sha for p, sha in mob.gitlinks("HEAD").items() if p.startswith("cores/")}
    own = dict(mob.BAT_DEFAULTS, RS_DIR=r"D:\build\rs", RS_VIVADO_OUT="D:/build/out")
    text = mob.build_bat("develop", mob.git("rev-parse", "HEAD"), links, own)
    assert 'if not defined RS_DIR set "RS_DIR=D:\\build\\rs"' in text.splitlines()
    assert 'if not defined RS_VIVADO_OUT set "RS_VIVADO_OUT=D:/build/out"' in text.splitlines()
    flat = " ".join(mob.readme("develop", mob.git("rev-parse", "HEAD"), links, {p: "" for p in links}, "0" * 40,
                               own).split())
    assert "(default D:\\build\\rs)" in flat and "set RS_VIVADO_OUT=D:/build/out2" in flat
