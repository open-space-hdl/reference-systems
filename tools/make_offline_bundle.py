# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Writes the repository and the sources a build needs as Git bundles, for a build computer without network access.

Usage: python tools/make_offline_bundle.py <out dir> [--ref main] [--bat-rs-dir <dir>] [--bat-vivado-out <dir>]

Writes the bundles, README.txt and build.bat to <out dir>. --bat-rs-dir and --bat-vivado-out set the defaults of
RS_DIR and RS_VIVADO_OUT in build.bat and README.txt, for the directories of the build computer.

Bundles: reference-systems.bundle (the branch --ref, default main), one bundle per submodule in cores/ at the commit
that --ref records (that of Open Logic FT with the tag of the systems and the tags their cores were verified with),
and en_cl_fix.bundle, the submodule 3rdParty/en_cl_fix of Open Logic FT, whose files the compile orders of the
systems list. The submodules of the cores themselves (their own open-logic and uvvm) serve the tests of
the cores and are not needed. README.txt in <out dir> lists the commits and the commands to clone and build. The
build commands of every board target are those of section 4 (Build) of its hardware test procedure
(systems/<system>/targets/<board>/docs/hardware_test.md at --ref), with the text in front of them, so that README.txt
and the procedure cannot differ. build.bat in <out dir> runs the same commands on Windows in one step: the clone from
the bundles next to it, the check of the pins and the build of one system, with RS_VIVADO_OUT set.

Before it writes the bundles, the script checks the pins against the published repositories (tools/check_pins.py).
Before it reports success, it clones the bundles into a temporary directory with the commands of README.txt, with
only file transports allowed, and checks every commit, the files of every compile order and the pins against the
tags that the bundles carry (tools/check_pins.py --offline, also with only file transports). The submodules in
cores/ must be checked out at the recorded commits.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
REPO = "reference-systems"
# Defaults of build.bat: the clone, the project directory of Vivado (short, see README.txt) and the installation of
# Vivado 2025.2 when vivado is not on the PATH; the options --bat-rs-dir and --bat-vivado-out replace the first two
BAT_DEFAULTS = {"RS_DIR": r"C:\git\reference-systems", "RS_VIVADO_OUT": "C:/git/rs",
                "VIVADO": r"C:\AMDDesignTools\2025.2\Vivado\bin\vivado.bat"}
BUILD_COMMAND = re.compile(r"vivado -mode batch -source systems/([^/]+)/targets/([^/]+)/tcl/build\.tcl"
                           r"(?: -tclargs (.+))?")
NESTED = ("cores/open-logic-ft", "3rdParty/en_cl_fix", "en_cl_fix")   # submodule, its submodule, bundle name
HARDWARE_TEST = re.compile(r"systems/([^/]+)/targets/([^/]+)/docs/hardware_test\.md")
BUILD_SECTION = re.compile(r"^## 4\. Build\n(.*?)^```shell\n(.*?)^```$", re.M | re.S)


def git(*args: str, cwd: Path = ROOT, env: dict | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} in {cwd} failed:\n{result.stdout}{result.stderr}")
    return result.stdout.strip()


def gitlinks(ref: str, path: str = "", cwd: Path = ROOT) -> dict[str, str]:
    """Paths and commits of the submodules that a commit records (below path)."""
    out = git("ls-tree", "-r", ref, *([path] if path else []), cwd=cwd)
    links = {}
    for line in out.splitlines():
        meta, p = line.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "commit":
            links[p] = sha
    return links


def olo_tags(ref: str) -> list[str]:
    """The tags of Open Logic FT that the system files at ref name: the tag that every system pins and the tags that
    its cores were verified with (verifiedWith). The bundle of Open Logic FT carries them all, since the check of the
    pins on the offline clone resolves each of them."""
    tags = set()
    for path in git("ls-tree", "-r", "--name-only", ref, "systems").splitlines():
        parts = path.split("/")
        if len(parts) == 3 and parts[2] == f"{parts[1]}.composer.json":
            for source in json.loads(git("show", f"{ref}:{path}"))["sources"]:
                tags.update(t for t in (source.get("tag"), source.get("verifiedWith")) if t)
    return sorted(tags)


def bundle(repo: Path, out: Path, refs: list[str]) -> None:
    out.unlink(missing_ok=True)
    git("bundle", "create", str(out), *refs, cwd=repo)
    git("bundle", "verify", str(out), cwd=repo)


def clone_commands(out: str, branch: str, submodules: list[str]) -> list[str]:
    """The commands of README.txt; <out> is the directory of the bundles."""
    lines = [f"git clone -b {branch} {out}/{REPO}.bundle {REPO}", f"cd {REPO}"]
    lines += [f"git config submodule.{p}.url {out}/{Path(p).name}.bundle" for p in submodules]
    lines += ["git -c protocol.file.allow=always submodule update --init",
              f"git -C {NESTED[0]} config submodule.{NESTED[1]}.url {out}/{NESTED[2]}.bundle",
              f"git -C {NESTED[0]} -c protocol.file.allow=always submodule update --init {NESTED[1]}"]
    return lines


def build_section(text: str, where: str) -> tuple[str, list[str]]:
    """The text in front of the first command block of section 4 (Build) of a hardware test procedure, as one
    paragraph without Markdown code marks, and the lines of that block."""
    match = BUILD_SECTION.search(text)
    if not match or "\n## " in match.group(1):
        raise SystemExit(f"{where}: section 4 (Build) has no shell command block")
    return " ".join(match.group(1).split()).replace("`", ""), match.group(2).splitlines()


def build_lines(ref: str) -> list[str]:
    """The build part of README.txt: per board target, section 4 of its hardware test procedure at ref."""
    lines = []
    for path in git("ls-tree", "-r", "--name-only", ref, "systems").splitlines():
        target = HARDWARE_TEST.fullmatch(path)
        if not target:
            continue
        intro, commands = build_section(git("show", f"{ref}:{path}"), f"{ref}:{path}")
        lines += ["", f"{target.group(1)} on the {target.group(2).upper()} ({path}, section 4):", ""]
        lines += textwrap.wrap(intro, 100, initial_indent="  ", subsequent_indent="  ")
        lines += [""] + ["    " + c for c in commands]
    if not lines:
        raise SystemExit(f"no hardware test procedure in systems/ at {ref}")
    return lines


def build_options(ref: str) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """Per board target at ref: system, board and the options of section 4 of its hardware test procedure, each as
    the comment line in front of its command and the words after -tclargs (empty for the default image)."""
    targets = []
    for path in git("ls-tree", "-r", "--name-only", ref, "systems").splitlines():
        target = HARDWARE_TEST.fullmatch(path)
        if not target:
            continue
        options, comment = [], ""
        for line in build_section(git("show", f"{ref}:{path}"), f"{ref}:{path}")[1]:
            command = BUILD_COMMAND.fullmatch(line.strip())
            if line.strip().startswith("#"):
                comment = line.strip().lstrip("#").strip()
            elif command:
                if command.group(1, 2) != target.group(1, 2):
                    raise SystemExit(f"{ref}:{path}: section 4 builds {command.group(1)} for {command.group(2)}")
                options.append((comment, command.group(3) or ""))
                comment = ""
        if not options:
            raise SystemExit(f"{ref}:{path}: section 4 has no command of tcl/build.tcl")
        targets.append((target.group(1), target.group(2), options))
    return targets


def build_bat(ref: str, ref_sha: str, links: dict[str, str], defaults: dict[str, str] = BAT_DEFAULTS) -> str:
    """build.bat of the package: the commands of README.txt on Windows in one step (clone from the bundles in the
    folder of the script when RS_DIR does not exist yet, check of the pins, build of one system with Vivado). A clone
    at another commit stops it; it deletes nothing."""
    targets = build_options(ref_sha)
    first, _, first_options = targets[0]
    calls = [f"rem   build.bat {first} {words}".rstrip() for _, words in first_options]
    width = max(len(c) for c in calls) + 2
    usage = [c.ljust(width) + comment for c, (comment, _) in zip(calls, first_options)]
    systems = ", ".join(f"{s} ({b})" for s, b, _ in targets)
    clone = [f'git clone -b %REF% "%PKG%/{REPO}.bundle" "%RS_DIR%" || exit /b 1', 'cd /d "%RS_DIR%" || exit /b 1']
    clone += [f'git config submodule.{p}.url "%PKG%/{Path(p).name}.bundle" || exit /b 1' for p in links]
    clone += ["git -c protocol.file.allow=always submodule update --init || exit /b 1",
              f'git -C {NESTED[0]} config submodule.{NESTED[1]}.url "%PKG%/{NESTED[2]}.bundle" || exit /b 1',
              f"git -C {NESTED[0]} -c protocol.file.allow=always submodule update --init {NESTED[1]} || exit /b 1"]
    lines = [
        "@echo off",
        f"rem Open Space HDL reference systems, offline package: {REPO} {ref} {ref_sha[:12]}",
        "rem",
        "rem The commands of README.txt in one step: clones the repository from the bundles in the folder of this",
        "rem script when RS_DIR does not exist yet, checks the pins without network access and builds one system with",
        "rem Vivado, with RS_VIVADO_OUT set. Confirm the reference clock on the board first (hardware_test.md of the",
        "rem target, section 5, step 2), then build the image of that option only:",
        "rem",
        "rem   build.bat <system> [<words of build.tcl>]",
        "rem",
        *usage,
        "rem",
        f"rem Systems: {systems}; the other systems take the same words.",
        "rem",
        "rem Settings: environment variables set before the call, else the defaults below.",
        "rem   RS_DIR         clone of the repository; an existing clone must be at the commit of this package",
        "rem   RS_VIVADO_OUT  project directory of Vivado, at most about 40 characters (write_device_image); the",
        "rem                  project of an earlier build in it is replaced",
        "rem   VIVADO         vivado.bat of Vivado 2025.2 (default: vivado on the PATH, else the path below)",
        "rem   RS_PYTHON      Python for the check of the pins (default: python, else the launcher py -3)",
        "setlocal EnableExtensions",
        *[f'if not defined {k} set "{k}={v}"' for k, v in defaults.items() if k != "VIVADO"],
        f'set "REF={ref}"',
        f'set "REF_SHA={ref_sha}"',
        'set "PKG=%~dp0"',
        'set "PKG=%PKG:~0,-1%"',
        'set "PKG=%PKG:\\=/%"',
        "",
        'set "SYSTEM=%~1"',
        'set "BOARD="',
        *[f'if /i "%SYSTEM%"=="{s}" set "BOARD={b}"' for s, b, _ in targets],
        "if not defined BOARD (",
        "    echo usage: build.bat ^<system^> [^<words of build.tcl^>]; systems: "
        + ", ".join(s for s, _, _ in targets),
        "    exit /b 2",
        ")",
        'set "WORDS="',
        ":words",
        "shift",
        'if "%~1"=="" goto words_done',
        'set "WORDS=%WORDS% %~1"',
        "goto words",
        ":words_done",
        "",
        "where git >nul 2>&1 || (echo git is not on the PATH & exit /b 2)",
        "if not defined VIVADO (",
        '    where vivado >nul 2>&1 && set "VIVADO=vivado"',
        ")",
        f'if not defined VIVADO if exist "{BAT_DEFAULTS["VIVADO"]}" set "VIVADO={BAT_DEFAULTS["VIVADO"]}"',
        "if not defined VIVADO (",
        "    echo Vivado not found: set VIVADO to vivado.bat of Vivado 2025.2, or run its settings64.bat first",
        "    exit /b 2",
        ")",
        "",
        'if exist "%RS_DIR%\\.git" goto have_clone',
        'if exist "%RS_DIR%" (',
        "    echo %RS_DIR% exists and is no clone: set RS_DIR to a new directory",
        "    exit /b 1",
        ")",
        "echo Cloning %REF% from %PKG% into %RS_DIR%",
        *clone,
        "goto check_clone",
        ":have_clone",
        'cd /d "%RS_DIR%" || exit /b 1',
        ":check_clone",
        'set "HEAD="',
        "for /f %%h in ('git rev-parse HEAD') do set \"HEAD=%%h\"",
        'if /i not "%HEAD%"=="%REF_SHA%" (',
        "    echo %RS_DIR% is at %HEAD%, this package is %REF% %REF_SHA%.",
        "    echo Rename or remove %RS_DIR%, or set RS_DIR to a new directory, and run build.bat again.",
        "    exit /b 1",
        ")",
        'set "SUBMODULES_OK=1"',
        "for /f \"delims=\" %%l in ('git submodule status') do (",
        "    echo %%l",
        '    echo %%l| findstr /b /r "[-+U]" >nul && set "SUBMODULES_OK="',
        ")",
        "if not defined SUBMODULES_OK (",
        "    echo A submodule is not at the recorded commit ^(leading - or + above^)",
        "    exit /b 1",
        ")",
        "if not defined RS_PYTHON (",
        '    python --version >nul 2>&1 && set "RS_PYTHON=python"',
        ")",
        "if not defined RS_PYTHON (",
        '    py -3 --version >nul 2>&1 && set "RS_PYTHON=py -3"',
        ")",
        "if not defined RS_PYTHON (",
        "    echo Python not found ^(python, py -3^): the check of the pins is skipped",
        ") else (",
        "    %RS_PYTHON% tools/check_pins.py --offline || (echo The pins do not match the bundles & exit /b 1)",
        ")",
        'set "CHANGED=0"',
        "for /f %%c in ('git status --porcelain ^| find /c /v \"\"') do set \"CHANGED=%%c\"",
        'if not "%CHANGED%"=="0" (',
        "    echo WARNING: %CHANGED% files of %RS_DIR% differ from the commit, and the build takes them:",
        "    git status --short",
        ")",
        "",
        "rem Vivado and the runs it starts at the same time without the user data of the Tcl Store: on a fresh",
        "rem installation they create its directory together, and one of them can fail at start-up (AMD AR 63253)",
        'set "XILINX_LOCAL_USER_DATA=no"',
        "echo Building %SYSTEM% for the %BOARD%, RS_VIVADO_OUT=%RS_VIVADO_OUT%, build.tcl words:%WORDS%",
        "if defined WORDS (",
        '    call "%VIVADO%" -mode batch -source systems/%SYSTEM%/targets/%BOARD%/tcl/build.tcl -tclargs%WORDS%',
        ") else (",
        '    call "%VIVADO%" -mode batch -source systems/%SYSTEM%/targets/%BOARD%/tcl/build.tcl',
        ")",
        "if errorlevel 1 (",
        "    echo The build failed: vivado.log in %RS_DIR% has the messages",
        "    exit /b 1",
        ")",
        "echo Results in %RS_VIVADO_OUT%/results",
        "endlocal",
        "exit /b 0",
    ]
    return "\r\n".join(lines) + "\r\n"


def clone_way_out(ref_sha: str, defaults: dict[str, str]) -> list[str]:
    """The lines of README.txt for a clone at another commit in RS_DIR, which stops build.bat: rename it, or build
    the package in new directories named after its commit."""
    rs_dir, out, sha = defaults["RS_DIR"], defaults["RS_VIVADO_OUT"], ref_sha[:7]
    return ["A clone at another commit in RS_DIR stops build.bat; it deletes nothing. Either rename or move that clone",
            "first, for example:", "",
            f"  ren {rs_dir} {PureWindowsPath(rs_dir).name}-<its commit>", "",
            "or build this package in new directories, set before the call (cmd, then PowerShell):", "",
            f"  set RS_DIR={rs_dir}-{sha}",
            f"  set RS_VIVADO_OUT={out}_{sha}",
            "  build.bat <system>", "",
            f'  $env:RS_DIR = "{rs_dir}-{sha}"',
            f'  $env:RS_VIVADO_OUT = "{out}_{sha}"',
            "  .\\build.bat <system>", ""]


def readme(ref: str, ref_sha: str, links: dict[str, str], tags: dict[str, str], nested_sha: str,
           defaults: dict[str, str] = BAT_DEFAULTS) -> str:
    """README.txt of the package: the bundles with their commits, the commands to clone and to build."""
    lines = [f"Open Space HDL reference systems, offline package: {REPO} {ref} {ref_sha[:12]}", "",
             f"{REPO}.bundle".ljust(30) + f"repository open-space-hdl/{REPO}, branch {ref}"]
    for p, sha in links.items():
        tag = f" (tag {tags[p]})" if tags[p] else ""
        lines.append(f"{Path(p).name}.bundle".ljust(30) + f"{p} at {sha[:12]}{tag}")
    lines.append(f"{NESTED[2]}.bundle".ljust(30) + f"{NESTED[0]}/{NESTED[1]} at {nested_sha[:12]}")
    lines += ["", "Clone (<path> is the directory of the bundles; git submodule status must show every commit without",
              "a leading - or +):", ""]
    lines += ["  " + c for c in clone_commands("<path>", ref, list(links))]
    lines += ["", "Then check the pins against the tags that the bundles carry, without network access; the package",
              "was written after the same check against the published repositories had passed:", "",
              "  python tools/check_pins.py --offline"]
    lines += ["", "Build and hardware test: systems/<system>/targets/<board>/docs/hardware_test.md, sections 3 to 9."]
    lines += build_lines(ref_sha)
    lines += ["",
              "On Windows keep the project path at most about 40 characters long, otherwise write_device_image fails",
              "while it compiles the platform loader firmware: set RS_VIVADO_OUT to a short directory (for example",
              "C:/git/rs) before starting Vivado. The results are written to <project>/results."]
    example = next((w for _, _, options in build_options(ref_sha) for _, w in options if w), "<words>")
    lines += ["",
              "build.bat in this folder runs these commands on Windows in one step: it clones the repository from the",
              f"bundles next to it into RS_DIR (default {defaults['RS_DIR']}) when that directory does not exist "
              "yet,",
              "checks the submodules and the pins (with python, else the launcher py -3, or RS_PYTHON), sets",
              "RS_VIVADO_OUT and builds one system:", "",
              "  build.bat <system>                       the default image",
              f"  build.bat <system> {example}".ljust(43) + "an option: the words after -tclargs above", "",
              *clone_way_out(ref_sha, defaults),
              "The build creates the project with create_project -force, so it replaces the project in RS_VIVADO_OUT",
              f"(default {defaults['RS_VIVADO_OUT']}). To keep an earlier project, set another short directory "
              "before the call,",
              f"for example: set RS_VIVADO_OUT={defaults['RS_VIVADO_OUT']}2. VIVADO selects vivado.bat when Vivado is "
              "not on the PATH."]
    return "\n".join(lines) + "\n"


def verify(out: Path, branch: str, ref_sha: str, links: dict[str, str], nested_sha: str) -> None:
    """Clones the bundles as README.txt says, with file transports only, and checks the result."""
    env = dict(os.environ, GIT_ALLOW_PROTOCOL="file", GIT_TERMINAL_PROMPT="0")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        repo = work / REPO
        git("clone", "-q", "-b", branch, str(out / f"{REPO}.bundle"), REPO, cwd=work, env=env)
        for p in links:
            git("config", f"submodule.{p}.url", (out / f"{Path(p).name}.bundle").as_posix(), cwd=repo, env=env)
        git("-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q", cwd=repo, env=env)
        nested = repo / NESTED[0]
        git("config", f"submodule.{NESTED[1]}.url", (out / f"{NESTED[2]}.bundle").as_posix(), cwd=nested, env=env)
        git("-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q", NESTED[1], cwd=nested, env=env)

        problems = []
        if git("rev-parse", "HEAD", cwd=repo) != ref_sha:
            problems.append(f"{REPO} is not at {ref_sha}")
        for p, sha in links.items():
            if git("rev-parse", "HEAD", cwd=repo / p) != sha:
                problems.append(f"{p} is not at {sha}")
        if git("rev-parse", "HEAD", cwd=nested / NESTED[1]) != nested_sha:
            problems.append(f"{NESTED[0]}/{NESTED[1]} is not at {nested_sha}")
        status = git("submodule", "status", cwd=repo)
        problems += [f"submodule status: {line}" for line in status.splitlines() if line[:1] in "-+U"]
        for order in sorted(repo.glob("systems/*/hdl/*_compile_order.txt")):
            missing = [line.split()[2] for line in order.read_text(encoding="utf-8").splitlines()
                       if line.startswith("file ") and not ((repo / "cores" / line.split()[2]).exists()
                                                            or (order.parent / line.split()[2]).exists())]
            problems += [f"{order.name}: missing {m}" for m in missing]
        pins = subprocess.run([sys.executable, "tools/check_pins.py", "--offline"], cwd=repo, capture_output=True,
                              text=True, env=env, check=False)
        if pins.returncode != 0:
            problems.append(f"check_pins.py --offline: {pins.stdout}{pins.stderr}")
        if problems:
            raise SystemExit("The offline clone is not complete:\n  " + "\n  ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="directory of the bundles (outside the repository)")
    parser.add_argument("--ref", default="main", help="branch of this repository to bundle (default main)")
    parser.add_argument("--bat-rs-dir", default=BAT_DEFAULTS["RS_DIR"],
                        help=f"default RS_DIR of build.bat, the clone (default {BAT_DEFAULTS['RS_DIR']})")
    parser.add_argument("--bat-vivado-out", default=BAT_DEFAULTS["RS_VIVADO_OUT"],
                        help=f"default RS_VIVADO_OUT of build.bat (default {BAT_DEFAULTS['RS_VIVADO_OUT']})")
    args = parser.parse_args()
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("write the bundles outside the repository")
    out.mkdir(parents=True, exist_ok=True)

    ref_sha = git("rev-parse", args.ref)
    links = {p: sha for p, sha in gitlinks(args.ref).items() if p.startswith("cores/")}
    for p, sha in links.items():
        head = git("rev-parse", "HEAD", cwd=ROOT / p)
        if head != sha:
            raise SystemExit(f"{p} is checked out at {head}, {args.ref} records {sha}: git submodule update")
    nested_sha = gitlinks("HEAD", NESTED[1], cwd=ROOT / NESTED[0])[NESTED[1]]
    if git("rev-parse", "HEAD", cwd=ROOT / NESTED[0] / NESTED[1]) != nested_sha:
        raise SystemExit(f"{NESTED[0]}/{NESTED[1]} is not checked out at {nested_sha}: git submodule update --init")

    build_lines(ref_sha)   # every hardware test procedure has its build commands
    build_options(ref_sha)
    pins = subprocess.run([sys.executable, "tools/check_pins.py"], cwd=ROOT, capture_output=True, text=True,
                          check=False)
    if pins.returncode != 0:
        raise SystemExit(f"check_pins.py against the published repositories failed:\n{pins.stdout}{pins.stderr}")

    bundle(ROOT, out / f"{REPO}.bundle", [args.ref])
    tags = {}
    for p, sha in links.items():
        tag = subprocess.run(["git", "describe", "--tags", "--exact-match", sha], cwd=ROOT / p, capture_output=True,
                             text=True, check=False).stdout.strip()
        tags[p] = tag
        names = [tag] if tag else []
        if p == NESTED[0]:
            names += [t for t in olo_tags(args.ref) if t != tag]
            for name in names:
                if subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{name}"], cwd=ROOT / p,
                                  capture_output=True, check=False).returncode != 0:
                    raise SystemExit(f"{p} has no tag {name}: git -C {p} fetch --tags")
        bundle(ROOT / p, out / f"{Path(p).name}.bundle", ["HEAD"] + [f"refs/tags/{n}" for n in names])
    bundle(ROOT / NESTED[0] / NESTED[1], out / f"{NESTED[2]}.bundle", ["HEAD"])

    verify(out, args.ref, ref_sha, links, nested_sha)

    defaults = dict(BAT_DEFAULTS, RS_DIR=args.bat_rs_dir, RS_VIVADO_OUT=args.bat_vivado_out)
    (out / "README.txt").write_text(readme(args.ref, ref_sha, links, tags, nested_sha, defaults), encoding="utf-8")
    (out / "build.bat").write_bytes(build_bat(args.ref, ref_sha, links, defaults).encode("ascii"))
    print(f"Bundles, README.txt and build.bat in {out}; offline clone checked: {REPO} {ref_sha[:12]}, "
          f"{len(links)} submodules, {NESTED[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
