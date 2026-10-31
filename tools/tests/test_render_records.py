"""The tables of a hardware test report rendered from a records file (tools/render_records.py)."""

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import render_records  # noqa: E402

RECORDS = {
    "system": "spfi_compress", "started": "2026-10-28 09:00:00", "finished": "2026-10-28 10:02:13",
    "target": "device", "profile": "lab", "seed": 1,
    "results": [{"test": "TC-RS-HW-01", "pass": True, "error": None},
                {"test": "TC-RS-HW-06", "pass": False, "error": "APID 0: compressed image 1: a | b"}],
    "records": [{"test": "TC-RS-HW-01", "text": "ofb0 lane 0: RX_RESETS 1"},
                {"test": "TC-RS-HW-06", "text": "model decoder: 2 compressed images decode to the images sent"}],
    "images": [{"test": "TC-RS-HW-06", "port": 0, "compressor": "ocp0", "nx": 64, "ny": 64, "nz": 48,
                "bytes": 260208, "sha256": "ab" * 32, "complete": True, "decoded": "equal"},
               {"test": "TC-RS-HW-06", "port": 0, "compressor": "ocp0", "nx": 64, "ny": 64, "nz": 48,
                "bytes": 248520, "sha256": "cd" * 32, "complete": True, "decoded": "equal"},
               {"test": "TC-RS-HW-08", "port": 3, "compressor": "ocp3", "nx": 64, "ny": 64, "nz": 48,
                "bytes": 1000, "sha256": "ef" * 32, "complete": False, "decoded": "aborted, not decoded"}],
    "rate": "40 images ... Gbit/s of samples", "decoded": 2, "undecoded": 0,
}


def test_the_tables_of_a_run():
    text = render_records.render(RECORDS)
    assert "| Profile, seed | lab, 1 |" in text
    assert "| TC-RS-HW-01 | Pass |  |" in text
    # a | in an error does not break the table
    assert "| TC-RS-HW-06 | Fail | APID 0: compressed image 1: a \\| b |" in text
    assert "#### TC-RS-HW-01\n\n- ofb0 lane 0: RX_RESETS 1" in text
    samples = 2 * 64 * 64 * 48
    assert f"| TC-RS-HW-06 | ocp0 | 2 | {samples} | 508728 | {8 * 508728 / samples:.3f} | 2 equal |" in text
    assert "| TC-RS-HW-08 | ocp3 | 1 (1 aborted) | 0 | 0 | - | 1 aborted, not decoded |" in text
    assert "Rate of TC-RS-HW-09" in text and "SHA-256" not in text


def test_the_digests_on_request():
    text = render_records.render(RECORDS, digests=True)
    assert f"| TC-RS-HW-06 | ocp0 | 1 | 64 x 64 x 48 | 248520 | `{'cd' * 32}` |" in text


def test_the_command_line(tmp_path):
    path = tmp_path / "records.json"
    path.write_text(json.dumps(RECORDS), encoding="utf-8")
    out = tmp_path / "tables.md"
    result = subprocess.run([sys.executable, str(TOOLS / "render_records.py"), str(path), "--out", str(out)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == render_records.render(RECORDS)
