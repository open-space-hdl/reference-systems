# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Hardware test of spfi_encrypt as pytest tests: one test per test case of hardware_test.py, in its order (the
plugin sw/refsys/hwplugin.py, options --target, --device, --profile, --seed, --results, --tc, --keep-going).

    python -B -m pytest systems/spfi_encrypt/sw/hwtest --target model --profile sim --results <directory>
"""

from pathlib import Path

import pytest

from refsys import hwplugin, systest

SYSTEM = "spfi_encrypt"
ht, sm = systest.system_modules(Path(__file__).resolve().parents[1], "hardware_test", "system_model")


def context(seed: int, profile: str):
    return ht.make_context(seed, ht.PROFILES[profile].packets)


def model(ctx):
    return sm.SystemModel(ctx.setup)


@pytest.mark.parametrize("test", hwplugin.parameters(ht))
def test_hardware(hw, test):
    hw.run(test)
