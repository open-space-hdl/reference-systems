# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Checks that the generated top level of every system (systems/<system>/hdl/) is the export of its Composer file.

Usage: python tools/check_hdl.py --composer <dir> [--write]

<dir> is a checkout of open-space-hdl/open-space-hdl.github.io at the commit in tools/composer_ref.txt. The export
is made with the command line tool of the Composer there (tools/composer_hdl.mjs, Node.js), which refuses a system
with errors. Every file in hdl/ must equal the exported file of the same name, and hdl/ holds no other file.
--write replaces hdl/ with the export instead of comparing.
"""

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "tools" / "composer_ref.txt"


def export(composer: Path, system_file: Path, out: Path) -> None:
    result = subprocess.run(["node", str(composer / "tools" / "composer_hdl.mjs"), str(system_file), str(out)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{system_file.name}: the Composer does not export it\n{result.stdout}{result.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--composer", type=Path, required=True, help="checkout of the website at composer_ref.txt")
    parser.add_argument("--write", action="store_true", help="replace hdl/ with the export")
    args = parser.parse_args()

    ref = REF.read_text(encoding="utf-8").split()[0]
    if (args.composer / ".git").exists():
        head = subprocess.run(["git", "-C", str(args.composer), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
        if head != ref:
            raise SystemExit(f"{args.composer} is at {head}, tools/composer_ref.txt names {ref}")

    failed = False
    for system_file in sorted(ROOT.glob("systems/*/*.composer.json")):
        hdl = system_file.parent / "hdl"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            export(args.composer, system_file, out)
            if args.write:
                shutil.rmtree(hdl, ignore_errors=True)
                shutil.copytree(out, hdl)
                print(f"{hdl.relative_to(ROOT).as_posix()}: written")
                continue
            exported = {p.name for p in out.iterdir()}
            present = {p.name for p in hdl.iterdir()} if hdl.is_dir() else set()
            problems = [f"missing {n}" for n in sorted(exported - present)]
            problems += [f"not exported {n}" for n in sorted(present - exported)]
            problems += [f"differs {n}" for n in sorted(exported & present)
                         if not filecmp.cmp(out / n, hdl / n, shallow=False)]
        if problems:
            failed = True
            print(f"{hdl.relative_to(ROOT).as_posix()}: " + "; ".join(problems))
        elif not args.write:
            print(f"{hdl.relative_to(ROOT).as_posix()}: equal to the export")
    if failed:
        print("Export the system again with the Composer of tools/composer_ref.txt and commit hdl/ with the system file.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
