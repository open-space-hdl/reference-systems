# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of the far-end link reset of the test equipment (StarSystemTransport.far_end_link_reset in
sw/refsys/transport.py) and of the check of its software (far_end_software), with a stand-in for the link control
of the SpaceFibre port: the hardware test needs no operator. The stand-in holds the link down while LINK_RESET is set
and makes it ready again some polls after its release."""

import sys
import types

import pytest

from refsys import transport
from refsys.transport import StarSystemTransport, TransportError

READY = 0x1


class Link:
    """Stand-in for the link control of the SpaceFibre ports of the device: link status = active lanes << 8 | ready."""

    def __init__(self, lanes=4, polls_to_ready=3, honours_reset=True):
        self.lanes, self.polls_to_ready, self.honours_reset = lanes, polls_to_ready, honours_reset
        self.reset = False
        self.recovering = 0              # polls until the link is ready again after a released reset
        self.calls = []

    def __call__(self):              # StarUltraPCIeInterface()
        return self

    def enableLinkReset(self, device, port):
        self.calls.append(("enable", device, port))
        self.reset = self.honours_reset

    def disableLinkReset(self, device, port):
        self.calls.append(("disable", device, port))
        if self.reset:
            self.recovering = self.polls_to_ready
        self.reset = False

    def getLinkStatus(self, device, port):
        if self.reset:
            return 0
        if self.recovering:
            self.recovering -= 1
            return 0
        return self.lanes << 8 | READY

    @staticmethod
    def isLinkReady(status):
        return bool(status & READY)

    @staticmethod
    def getActiveLanesCount(status):
        return status >> 8


@pytest.fixture
def far_end(monkeypatch):
    """The link control of a device in sys.modules, and a transport of the device on SpaceFibre port 2."""
    def install(link, lib=True):
        package = types.ModuleType("STAR_Ultra")
        interface = types.ModuleType("STAR_Ultra.interface")
        interface.StarUltraPCIeInterface = link
        interface.star_ultra_lib = object() if lib else None
        package.interface = interface
        monkeypatch.setitem(sys.modules, "STAR_Ultra", package)
        monkeypatch.setitem(sys.modules, "STAR_Ultra.interface", interface)
        device = StarSystemTransport.__new__(StarSystemTransport)
        device._device_id, device._spfi_port = 7, 2
        return device
    return install


def test_the_far_end_resets_its_link_and_waits_until_it_is_ready_again(far_end):
    link = Link()
    result = far_end(link).far_end_link_reset(hold=0.001, timeout=1.0)
    assert link.calls == [("enable", 7, 2), ("disable", 7, 2)]
    assert result.down
    assert result.text.startswith("SpaceFibre port 2 of the device: 4 active lanes before the link reset, 0 and link "
                                  "not ready while it was held for 0.001 s, ready with 4 lanes ")


def test_a_far_end_whose_link_stays_ready_in_reset_is_reported(far_end):
    result = far_end(Link(honours_reset=False)).far_end_link_reset(hold=0.001, timeout=1.0)
    assert not result.down and "link ready while it was held" in result.text


def test_a_link_that_does_not_come_back_is_a_transport_error(far_end):
    link = Link(polls_to_ready=10 ** 9)
    with pytest.raises(TransportError, match="is not ready with 4 lanes 0.05 s after its link reset"):
        far_end(link).far_end_link_reset(hold=0.001, timeout=0.05)
    assert link.calls[-1] == ("disable", 7, 2)


def test_the_reset_is_released_also_when_the_hold_fails(far_end, monkeypatch):
    link = Link()
    device = far_end(link)
    monkeypatch.setattr(transport.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        device.far_end_link_reset(hold=0.001)
    assert link.calls == [("enable", 7, 2), ("disable", 7, 2)]


@pytest.mark.parametrize("modules, message", [
    ({}, "not installed"),
    ({"STAR_system": types.ModuleType("STAR_system")}, "installed, without the link control of the SpaceFibre port"),
])
def test_the_check_reports_a_far_end_software_without_link_control(monkeypatch, modules, message):
    for name in ("STAR_system", "STAR_Ultra", "STAR_Ultra.interface"):
        monkeypatch.setitem(sys.modules, name, modules.get(name))
    with pytest.raises(ImportError, match=message):
        transport.far_end_software()


def test_the_check_reports_a_link_control_whose_library_does_not_load(far_end, monkeypatch):
    far_end(Link(), lib=False)
    monkeypatch.setitem(sys.modules, "STAR_system", types.ModuleType("STAR_system"))
    with pytest.raises(ImportError, match="the library of the link control of the SpaceFibre port does not load"):
        transport.far_end_software()
    far_end(Link(), lib=True)
    assert transport.far_end_software() == "found"
