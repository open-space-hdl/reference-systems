# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""pytest plugin of the tests of the framework (sw/tests/test_hwplugin.py), loaded with -p hwfaults: --fault
TC-RS-HW-nn makes that test case fail as a failing check would, so that the tests see what the framework does after a
failure."""

import pytest

from refsys import systest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--fault", help="test case that fails (tests of the framework only)")


def pytest_configure(config: pytest.Config) -> None:
    fault = config.getoption("fault")
    if not fault:
        return
    run_one = systest.run_one

    def failing(regs, ctx, test):
        if systest.test_name(test) == fault:
            systest.check(False, f"{fault}: fault of the framework test")
        return run_one(regs, ctx, test)

    systest.run_one = failing
