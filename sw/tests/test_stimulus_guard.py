# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Guard of the simulation evidence: the hardware tests make exactly the RMAP requests and exchanges of the code on
which the system simulations of the evidence ran. For every simulation of the evidence, the stimuli that the bench
runner writes now (stimulus.txt and vectors.json, profile sim, seed 1) have the SHA-256 that data/stimulus_sha256.json
records for that code. A change of a request changes a digest: the simulation of that group then runs again and the
file gets its new digests with the code of the new run.

The stimuli of the test cases with the images of the lab take minutes; the guard runs with RS_STIMULUS_GUARD=1."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DIGESTS = json.loads((HERE / "data" / "stimulus_sha256.json").read_text(encoding="utf-8"))
GROUPS = {k: v for k, v in DIGESTS.items() if not k.startswith("_")}

pytestmark = pytest.mark.skipif(os.environ.get("RS_STIMULUS_GUARD") != "1",
                                reason="the guard of the simulation evidence runs with RS_STIMULUS_GUARD=1")


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_the_stimuli_equal_those_of_the_evidence(group, tmp_path):
    g = GROUPS[group]
    done = subprocess.run([sys.executable, "-B", str(HERE / "stimulus_digest.py"), g["system"], str(tmp_path),
                           *g["tests"].split()], capture_output=True, text=True, timeout=1800, check=False)
    assert done.returncode == 0, done.stderr[-3000:]
    digests = dict(line.split() for line in done.stdout.splitlines()
                   if line.split()[:1] in (["stimulus.txt"], ["vectors.json"]))
    assert digests == {"stimulus.txt": g["stimulus"], "vectors.json": g["vectors"]}, (
        f"{group}: the stimuli differ from those of the evidence on {DIGESTS['_code']}")
