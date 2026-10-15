# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Writes the repository and the sources a build needs as Git bundles, for a build computer without network access.

Usage: python tools/make_offline_bundle.py <out dir> [--ref main]

Bundles: reference-systems.bundle (the branch --ref, default main), one bundle per submodule in cores/ at the commit
that --ref records, and en_cl_fix.bundle, the submodule 3rdParty/en_cl_fix of Open Logic FT, whose files the compile
orders of the systems list. The submodules of the cores themselves (their own open-logic and uvvm) serve the tests of
the cores and are not needed. README.txt in <out dir> lists the commits and the commands to clone and build.

Before it reports success, the script clones the bundles into a temporary directory with the commands of README.txt,
with only file transports allowed, and checks every commit, the files of every compile order and the pins
(tools/check_pins.py). The submodules in cores/ must be checked out at the recorded commits.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "reference-systems"
NESTED = ("cores/open-logic-ft", "3rdParty/en_cl_fix", "en_cl_fix")   # submodule, its submodule, bundle name


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
        pins = subprocess.run([sys.executable, "tools/check_pins.py"], cwd=repo, capture_output=True, text=True,
                              check=False)
        if pins.returncode != 0:
            problems.append(f"check_pins.py: {pins.stdout}{pins.stderr}")
        if problems:
            raise SystemExit("The offline clone is not complete:\n  " + "\n  ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="directory of the bundles (outside the repository)")
    parser.add_argument("--ref", default="main", help="branch of this repository to bundle (default main)")
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

    bundle(ROOT, out / f"{REPO}.bundle", [args.ref])
    tags = {}
    for p, sha in links.items():
        tag = subprocess.run(["git", "describe", "--tags", "--exact-match", sha], cwd=ROOT / p, capture_output=True,
                             text=True, check=False).stdout.strip()
        tags[p] = tag
        bundle(ROOT / p, out / f"{Path(p).name}.bundle", ["HEAD"] + ([f"refs/tags/{tag}"] if tag else []))
    bundle(ROOT / NESTED[0] / NESTED[1], out / f"{NESTED[2]}.bundle", ["HEAD"])

    verify(out, args.ref, ref_sha, links, nested_sha)

    lines = [f"Open Space HDL reference systems, offline package: {REPO} {args.ref} {ref_sha[:12]}", "",
             f"{REPO}.bundle".ljust(30) + f"repository open-space-hdl/{REPO}, branch {args.ref}"]
    for p, sha in links.items():
        lines.append(f"{Path(p).name}.bundle".ljust(30) + f"{p} at {sha[:12]}" + (f" (tag {tags[p]})" if tags[p] else ""))
    lines.append(f"{NESTED[2]}.bundle".ljust(30) + f"{NESTED[0]}/{NESTED[1]} at {nested_sha[:12]}")
    lines += ["", "Clone (<path> is the directory of the bundles; git submodule status must show every commit without",
              "a leading - or +):", ""]
    lines += ["  " + c for c in clone_commands("<path>", args.ref, list(links))]
    lines += ["", "Build and hardware test: systems/<system>/targets/<board>/docs/hardware_test.md, sections 3 to 7.",
              "For spfi_encrypt on the VCK190 (from the repository root):", "",
              "  vivado -mode batch -source systems/spfi_encrypt/targets/vck190/tcl/build.tcl", "",
              "On Windows keep the project path at most about 40 characters long, otherwise write_device_image fails",
              "while it compiles the platform loader firmware: set RS_VIVADO_OUT to a short directory (for example",
              "C:/rs) before starting Vivado. The results are written to <project>/results."]
    (out / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Bundles and README.txt in {out}; offline clone checked: {REPO} {ref_sha[:12]}, "
          f"{len(links)} submodules, {NESTED[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
