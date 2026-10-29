# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""pytest configuration of the repository: the plugin of the hardware tests (sw/refsys/hwplugin.py), whose tests in
systems/<system>/sw/hwtest are collected only with --target."""

pytest_plugins = ["refsys.hwplugin"]
