# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Renders the tables of a hardware test report from the records file of a run (the JSON file that
systems/<system>/sw/hardware_test.py writes with --records): the run, the result of every test case, the values
recorded per test case and the compressed images per test case and channel with their decoding check. The tables go
into the report of the system (sections "Results per test case" and "Records"); the records file itself stays with
the records of the test run.

Usage: python tools/render_records.py <records file> [--digests] [--out <Markdown file>]

--digests adds the SHA-256 of every compressed image. The output holds what the records file holds: the script that
writes it records no host name and no path.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def run_table(data: dict) -> list[str]:
    return ["| Item | Value |", "| --- | --- |",
            f"| System | {data['system']} |",
            f"| Run | {data['started']} to {data['finished']} |",
            f"| Target | {data['target']} |",
            f"| Profile, seed | {data['profile']}, {data['seed']} |"]


def results_table(data: dict) -> list[str]:
    """Every test case that ran, then those that did not run with the reason (pytest runs: the first failure stops
    the run, the later test cases build on the earlier ones)."""
    out = ["| Test case | Result | Error |", "| --- | --- | --- |"]
    for r in data["results"]:
        error = (r["error"] or "").replace("|", "\\|")
        out.append(f"| {r['test']} | {'Pass' if r['pass'] else 'Fail'} | {error} |")
    for r in data.get("not_run", []):
        out.append(f"| {r['test']} | Not run | {r['reason']} |")
    return out


def requirements_table(data: dict) -> list[str]:
    """The requirements that every test case confirms (the markers of the pytest tests), with its result."""
    requirements = data.get("requirements") or {}
    if not requirements:
        return []
    result = {r["test"]: "Pass" if r["pass"] else "Fail" for r in data["results"]}
    result |= {r["test"]: "Not run" for r in data.get("not_run", [])}
    out = ["| Test case | Result | Requirements |", "| --- | --- | --- |"]
    for test, ids in requirements.items():
        if ids:
            out.append(f"| {test} | {result.get(test, 'Not selected')} | {', '.join(ids)} |")
    return out


def records_list(data: dict) -> list[str]:
    by_test = defaultdict(list)
    for r in data["records"]:
        by_test[r["test"]].append(r["text"])
    out = []
    for test, texts in by_test.items():
        out += [f"#### {test}", ""] + [f"- {t}" for t in texts] + [""]
    return out[:-1] if out else ["None."]


def images_table(data: dict) -> list[str]:
    """Per test case and channel: the images, their samples and compressed bytes, the bits per sample and the
    results of the decoding check."""
    groups = defaultdict(list)
    for image in data.get("images", []):
        groups[(image["test"], image["port"])].append(image)
    out = ["| Test case | Channel | Images | Samples | Compressed bytes | Bits per sample | Decoding check |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for (test, port), images in groups.items():
        samples = sum(i["nx"] * i["ny"] * i["nz"] for i in images if i["complete"])
        compressed = sum(i["bytes"] for i in images if i["complete"])
        bps = f"{8 * compressed / samples:.3f}" if samples else "-"
        checks = defaultdict(int)
        for i in images:
            checks[i["decoded"]] += 1
        decoded = ", ".join(f"{n} {what}" for what, n in sorted(checks.items()))
        aborted = sum(not i["complete"] for i in images)
        count = f"{len(images)}" + (f" ({aborted} aborted)" if aborted else "")
        out.append(f"| {test} | {images[0]['compressor']} | {count} | {samples} | {compressed} | {bps} | {decoded} |")
    return out


def digests_table(data: dict) -> list[str]:
    out = ["| Test case | Channel | Image | Dimensions | Bytes | SHA-256 |", "| --- | --- | --- | --- | --- | --- |"]
    count = defaultdict(int)
    for i in data.get("images", []):
        n = count[(i["test"], i["port"])]
        count[(i["test"], i["port"])] += 1
        out.append(f"| {i['test']} | {i['compressor']} | {n} | {i['nx']} x {i['ny']} x {i['nz']} | {i['bytes']} | "
                   f"`{i['sha256']}` |")
    return out


def render(data: dict, digests: bool = False) -> str:
    lines = ["### Run", ""] + run_table(data)
    if data.get("keep_going"):
        lines += ["", "This run continued after a failure (--keep-going): a run for diagnosis, not evidence."]
    lines += ["", "### Results per test case", ""] + results_table(data)
    if data.get("requirements_note"):
        lines += ["", data["requirements_note"]]
    if requirements_table(data):
        lines += ["", "### Requirements per test case", ""] + requirements_table(data)
    lines += ["", "### Recorded values", ""] + records_list(data)
    lines += ["", "### Compressed images", ""] + images_table(data)
    if data.get("rate"):
        lines += ["", f"Rate of TC-RS-HW-09: {data['rate']}"]
    if digests:
        lines += ["", "### Digests of the compressed images", ""] + digests_table(data)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", type=Path, help="records file of a run (JSON)")
    parser.add_argument("--digests", action="store_true", help="add the SHA-256 of every compressed image")
    parser.add_argument("--out", type=Path, help="Markdown file to write (default: standard output)")
    args = parser.parse_args()
    text = render(json.loads(args.records.read_text(encoding="utf-8")), args.digests)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
