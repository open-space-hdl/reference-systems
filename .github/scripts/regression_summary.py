# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Summary of the system regression: one row per group from the results.xml of its artifact.

Usage: python -B .github/scripts/regression_summary.py <artifact directory> <groups as JSON list>

<artifact directory> holds the results.xml of every group at any depth (one directory per artifact, or the files of a
single artifact directly); the name of its test suite names the group. Prints a Markdown table
(group, system, exit code, test cases passed of all, failures, errors, simulated time, wall-clock time) and appends it
to $GITHUB_STEP_SUMMARY when set. Exit code 1 when a group has no results.xml, a failure, an error or a non-zero exit
code of its run.
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    root, groups = Path(sys.argv[1]), json.loads(sys.argv[2])
    rows = ["| Group | System | Exit code | Passed | Failures | Errors | Simulated | Wall clock |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    bad = []
    suites = {}
    for xml in sorted(root.rglob("results.xml")):
        suite = ET.parse(xml).getroot()
        suites[suite.get("name", "").removeprefix("reference-systems.")] = suite
    for group in groups:
        suite = suites.get(group)
        if suite is None:
            rows.append(f"| {group} | | no results.xml | | | | | |")
            bad.append(group)
            continue
        props = {p.get("name"): p.get("value") for p in suite.iter("property")}
        tests, failures, errors = (int(suite.get(k, "0")) for k in ("tests", "failures", "errors"))
        code = int(props.get("exit_code", "1"))
        rows.append(f"| {group} | {props.get('system', '')} | {code} | {tests - failures - errors} of {tests} | "
                    f"{failures} | {errors} | {float(props.get('simulated_us', 0)):.1f} us | "
                    f"{float(suite.get('time', 0)) / 60:.1f} min |")
        if code or failures or errors:
            bad.append(group)
    text = "\n".join(rows) + "\n\n" + (f"Groups that did not pass: {', '.join(bad)}\n" if bad else
                                        f"All {len(groups)} groups passed.\n")
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## System regression\n\n" + text)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
