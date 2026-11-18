# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Writes results.xml (JUnit) of one group of the system regression from the report.txt of its run.

Usage: python -B .github/scripts/regression_junit.py <group directory> <group name> <system> <exit code of run_ghdl.py>

The group directory is RS_GHDL_OUT of the group (runs/<run>/report.txt below it). Every test case row of the report
("TC-RS-HW-nn  PASS|FAIL  <simulated> us  <wall clock> min") becomes a test case; a group without report.txt, or with
a non-zero exit code and no failed row, gets one error. Prints the counts as one line.
"""

import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROW = re.compile(r"^(TC-RS-HW-\d\d)\s+(\S+)\s+([\d.]+) us\s+([\d.]+) min", re.M)


def main() -> int:
    directory, group, system, code = Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
    reports = sorted((directory / "runs").glob("*/report.txt"))
    cases, errors, sim_us, wall_min = [], [], 0.0, 0.0
    if len(reports) != 1:
        errors.append(f"{len(reports)} report.txt files below {directory / 'runs'} (expected 1)")
    else:
        text = reports[0].read_text(encoding="utf-8", errors="replace")
        cases = ROW.findall(text)
        m = re.search(r"Total simulated time ([\d.]+) us", text)
        sim_us = float(m.group(1)) if m else 0.0
        m = re.search(r"Total wall-clock time ([\d.]+) min", text)
        wall_min = float(m.group(1)) if m else 0.0
        if not cases:
            errors.append("report.txt has no test case rows")
    failures = [c for c in cases if c[1] != "PASS"]
    if code != 0 and not failures and not errors:
        errors.append(f"run_ghdl.py ended with exit code {code}")
    lines = [f'<testsuite name={quoteattr("reference-systems." + group)} tests="{len(cases) + len(errors)}" '
             f'failures="{len(failures)}" errors="{len(errors)}" skipped="0" time="{wall_min * 60:.1f}">',
             "  <properties>",
             f'    <property name="system" value={quoteattr(system)}/>',
             f'    <property name="simulated_us" value="{sim_us:.1f}"/>',
             f'    <property name="exit_code" value="{code}"/>',
             f'    <property name="report" value={quoteattr(str(reports[0]) if len(reports) == 1 else "")}/>',
             "  </properties>"]
    for name, result, sim, wall in cases:
        lines.append(f'  <testcase classname={quoteattr(system)} name="{name}" time="{float(wall) * 60:.1f}">')
        if result != "PASS":
            lines.append(f'    <failure message={quoteattr(f"{name} {result} at {sim} us of simulated time")}/>')
        lines.append("  </testcase>")
    for k, err in enumerate(errors):
        lines.append(f'  <testcase classname={quoteattr(system)} name="run_{k}" time="0">')
        lines.append(f"    <error message={quoteattr(err)}>{escape(err)}</error>")
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    (directory / "results.xml").write_text('<?xml version="1.0" encoding="utf-8"?>\n' + "\n".join(lines) + "\n",
                                           encoding="utf-8")
    passed = len(cases) - len(failures)
    print(f"tests {len(cases) + len(errors)} passed {passed} failed {len(failures)} errors {len(errors)} "
          f"simulated {sim_us:.1f} us wall {wall_min:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
