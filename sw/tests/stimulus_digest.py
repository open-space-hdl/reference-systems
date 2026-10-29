# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Writes the stimuli of one system simulation (profile sim, seed 1) as the bench runner does and prints the SHA-256
of stimulus.txt and vectors.json, one per line (for sw/tests/test_stimulus_guard.py). Python writes the two text files
with the line ends of the platform (CRLF on Windows, LF on Linux), so the digest is taken over the content with LF line
ends: it is the same on every platform.

Usage: python -B sw/tests/stimulus_digest.py <system> <directory> [TC-RS-HW-nn ...]
"""

import hashlib
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    system, directory, tests = sys.argv[1], Path(sys.argv[2]), sys.argv[3:] or None
    sys.path.insert(0, str(ROOT / "systems" / system / "sim"))
    importlib.import_module("vectors").write(directory, "sim", 1, tests)
    for name in ("stimulus.txt", "vectors.json"):
        content = (directory / name).read_bytes().replace(b"\r\n", b"\n")
        print(f"{name} {hashlib.sha256(content).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
