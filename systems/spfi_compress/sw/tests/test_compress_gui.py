# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of the logic of the interactive tool of spfi_compress (compress_gui.Session): offline with the model only,
and with the software model of the system in place of the far end."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from refsys import ocp, systest

HERE = Path(__file__).resolve().parent
ht, sm, gui = systest.system_modules(HERE.parent, "hardware_test", "system_model", "compress_gui")

SYSTEM = ht.System(dims=(8, 4, 3), chunk_bytes=256)


def session(online: bool = False):
    transport = sm.SystemModel(ht.Setup(), system=SYSTEM) if online else None
    return gui.Session(transport, seed=2, system=SYSTEM)


def test_the_tool_offers_the_channels_with_the_runtime_configuration():
    s = session()
    assert [ch.port for ch in s.channels()] == [0, 3]
    fields = s.fields(0)
    assert fields[:3] == ["nx", "ny", "nz"] and "abs_limit" not in fields
    if ocp.runtime_params(s.channel(0).generics):
        assert {"tinc", "umax", "k"} <= set(fields)
    assert "abs_limit" in s.fields(3)


def test_offline_the_model_compresses_and_decodes():
    s = session()
    values = s.values(3, {"nx": 5, "ny": 2, "nz": 3, "abs_limit": 7})
    result = s.run(3, values, s.pattern(3, values, "scene", 4))
    assert result.received is None and result.expected and not result.problems
    assert (result.cfg["large_n_x"], result.cfg["large_n_z"], result.cfg["a_vector"]) == (5, 3, [7])
    assert "bits per sample" in result.summary()[0]
    if ocp.decoder_available():
        assert result.decoded is not None and len(result.decoded) == 3


def test_online_the_chunks_equal_the_model_and_the_values_are_taken():
    s = session(online=True)
    values = s.values(0, {"nx": 4, "ny": 3, "nz": 2})
    result = s.run(0, values, s.pattern(0, values, "noise", 1))
    assert result.received == result.expected and not result.problems
    assert result.status == (False, False)
    again = s.run(0, values, s.pattern(0, values, "flat", 2))
    assert again.received == again.expected and s.images == 2


def test_a_refused_value_is_shown_and_sent_only_on_request():
    s = session(online=True)
    values = s.values(0, {"nx": 1})                   # full prediction: at least two samples wide
    assert s.problems(0, values) == ["NX 1 is out of range"]
    result = s.run(0, values, s.pattern(0, s.defaults(0), "scene", 0))
    assert result.refused and result.status is None
    result = s.run(0, values, s.pattern(0, s.defaults(0), "scene", 0), send_refused=True)
    assert result.refused and result.status == (True, False)


@pytest.mark.skipif(not ocp.runtime_params(ht.System().channels[0].generics),
                    reason="ocp0 has no runtime parameters in this system file")
def test_a_refused_parameter_names_the_value_the_core_would_take():
    s = session()
    values = s.values(0, {"tinc": 3})
    assert s.problems(0, values) == ["log2 t_inc 3 is out of range (the core would take 4)"]


def test_restore_commits_the_values_of_the_generics(tmp_path):
    s = session(online=True)
    s.run(3, s.values(3, {"nx": 2, "ny": 2, "nz": 2, "abs_limit": 1}), s.pattern(3, s.values(
        3, {"nx": 2, "ny": 2, "nz": 2, "abs_limit": 1}), "scene", 3))
    result = s.restore(3)
    assert result.received == result.expected and result.values == s.defaults(3)
    path = s.record(tmp_path, result, "pattern scene seed 0")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["target"] == "device" and data["channel"] == 3 and data["values"]["nx"] == 8
    assert str(tmp_path) not in path.read_text(encoding="utf-8")


def test_the_image_files():
    img = [[[z * 100 + y * 10 + x for x in range(3)] for y in range(2)] for z in range(4)]
    bsq = b"".join(v.to_bytes(2, "big") for band in img for row in band for v in row)
    assert gui.read_raw(bsq, 3, 2, 4, "BSQ", 2, True) == img
    bip = b"".join(img[z][y][x].to_bytes(2, "little") for y in range(2) for x in range(3) for z in range(4))
    assert gui.read_raw(bip, 3, 2, 4, "BIP", 2, False) == img
    bil = bytes(img[z][y][x] % 256 for y in range(2) for z in range(4) for x in range(3))
    assert gui.read_raw(bil, 3, 2, 4, "BIL", 1, True) == [[[v % 256 for v in row] for row in band] for band in img]
    ppm = b"P6\n# test\n2 1\n255\n" + bytes([1, 2, 3, 4, 5, 6])
    assert gui.read_pnm(ppm) == [[[1, 4]], [[2, 5]], [[3, 6]]]
    with pytest.raises(ValueError):
        gui.read_raw(bsq[:-1], 3, 2, 4, "BSQ", 2, True)
    assert gui.pgm([[0, 5], [10, 10]]).startswith(b"P5 2 2 255\n")


def test_the_window_opens_where_a_display_exists():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        photo = tk.PhotoImage(data=gui.pgm([[0, 1], [2, 3]]), format="PPM")
        assert (photo.width(), photo.height()) == (2, 2)
    finally:
        root.destroy()
