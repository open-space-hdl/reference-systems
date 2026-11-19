# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of the far-end link reset of the test equipment (link_reset and StarSystemTransport.far_end_link_reset in
sw/refsys/transport.py, the child process refsys.far_end_link) and of the check of its software (far_end_software),
with a stand-in for the link control of the SpaceFibre port: the hardware test needs no operator. The stand-in holds
the link down while LINK_RESET is set and makes it ready again some polls after its release. The link control needs
channel 0 of the device, which the transport closes for the child process and opens again after it."""

import json
import subprocess
import sys
import textwrap
import types

import pytest

from refsys import far_end_link, transport
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
    """The link control of a device in sys.modules (the far-end software of the test equipment)."""
    def install(link, lib=True):
        package = types.ModuleType("STAR_Ultra")
        interface = types.ModuleType("STAR_Ultra.interface")
        interface.StarUltraPCIeInterface = link
        interface.star_ultra_lib = object() if lib else None
        package.interface = interface
        monkeypatch.setitem(sys.modules, "STAR_Ultra", package)
        monkeypatch.setitem(sys.modules, "STAR_Ultra.interface", interface)
    return install


def test_the_far_end_resets_its_link_and_waits_until_it_is_ready_again():
    link = Link()
    result = transport.link_reset(link, 7, 2, hold=0.001, timeout=1.0)
    assert link.calls == [("enable", 7, 2), ("disable", 7, 2)]
    assert result.down
    assert result.text.startswith("SpaceFibre port 2 of the device: 4 active lanes before the link reset, 0 and link "
                                  "not ready while it was held for 0.001 s, ready with 4 lanes ")


def test_a_far_end_whose_link_stays_ready_in_reset_is_reported():
    result = transport.link_reset(Link(honours_reset=False), 7, 2, hold=0.001, timeout=1.0)
    assert not result.down and "link ready while it was held" in result.text


def test_a_link_that_does_not_come_back_is_a_transport_error():
    link = Link(polls_to_ready=10 ** 9)
    with pytest.raises(TransportError, match="is not ready with 4 lanes 0.05 s after its link reset"):
        transport.link_reset(link, 7, 2, hold=0.001, timeout=0.05)
    assert link.calls[-1] == ("disable", 7, 2)


def test_the_reset_is_released_also_when_the_hold_fails(monkeypatch):
    link = Link()
    monkeypatch.setattr(transport.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        transport.link_reset(link, 7, 2, hold=0.001)
    assert link.calls == [("enable", 7, 2), ("disable", 7, 2)]


# --- the channels of the device around the child process --------------------------------------------------------------
class Device:
    """Stand-in for a STAR-System device whose channel 0 is busy for `busy` attempts to open it after a close (the
    device frees it some time after the end of the child process). Every getChannels() gives new channel objects."""

    def __init__(self, channels=3, busy=0):
        self.count, self.busy = channels, busy
        self.events = []
        self.channel0_open = True

    def getChannels(self):
        return [Channel(self, i) for i in range(self.count)]


class Channel:
    def __init__(self, device, index):
        self.device, self.index = device, index

    def close(self):
        self.device.events.append(("close", self.index))
        if self.index == 0:
            self.device.channel0_open = False

    def openChannelToDevice(self, direction):
        if self.index == 0 and self.device.busy:
            self.device.busy -= 1
            self.device.events.append(("busy", 0))
            raise RuntimeError("Failed to open channel.")
        self.device.events.append(("open", self.index))
        if self.index == 0:
            self.device.channel0_open = True


def device_transport(device):
    """A StarSystemTransport of `device` on SpaceFibre port 2, its channels open."""
    t = StarSystemTransport.__new__(StarSystemTransport)
    t._enums = types.SimpleNamespace(STAR_CHANNEL_DIRECTION=types.SimpleNamespace(INOUT="inout"))
    t._device, t._device_name, t._device_id, t._spfi_port = device, "STAR-Ultra 1", 7, 2
    t._channels = device.getChannels()
    return t


def child(device, answer, returncode=0, stderr=""):
    """A stand-in for subprocess.run that checks that channel 0 is closed while the child runs."""
    def run(command, **kwargs):
        assert not device.channel0_open, "channel 0 must be closed while the link control runs"
        device.events.append(("child", tuple(command[3:])))
        return subprocess.CompletedProcess(command, returncode, stdout=f"noise\n{json.dumps(answer)}\n",
                                           stderr=stderr)
    return run


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(transport, "CHANNEL_REOPEN_INTERVAL", 0.001)


def test_channel_0_is_closed_for_the_child_and_opened_again_after_it(monkeypatch, fast):
    device = Device(busy=2)
    t = device_transport(device)
    monkeypatch.setattr(transport.subprocess, "run", child(device, {"down": True, "text": "reset seen"}))
    result = t.far_end_link_reset(hold=0.1, timeout=5.0)
    assert result == transport.FarEndReset(True, "reset seen")
    assert device.events == [("close", 0), ("child", ("refsys.far_end_link", "STAR-Ultra 1", "2", "0.1", "5.0")),
                             ("busy", 0), ("busy", 0), ("open", 0)]
    assert device.channel0_open and t._channels[0] is not None


def test_a_failed_link_control_is_a_transport_error_and_channel_0_opens_again(monkeypatch, fast):
    device = Device()
    t = device_transport(device)
    monkeypatch.setattr(transport.subprocess, "run", child(device, {"error": "Could not get the link status."}, 1))
    with pytest.raises(TransportError, match="link control of the far end failed: Could not get the link status"):
        t.far_end_link_reset()
    assert device.events[-1] == ("open", 0)


def test_a_child_without_a_result_is_reported_with_its_error_output(monkeypatch, fast):
    device = Device()
    t = device_transport(device)

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Traceback ...\nImportError: no STAR")
    monkeypatch.setattr(transport.subprocess, "run", run)
    with pytest.raises(TransportError, match="ended without a result \\(exit 1\\): Traceback ...\nImportError"):
        t.far_end_link_reset()
    assert device.channel0_open


def test_a_channel_that_stays_busy_is_a_transport_error(monkeypatch, fast):
    monkeypatch.setattr(transport, "CHANNEL_REOPEN_TIMEOUT", 0.02)
    device = Device(busy=10 ** 9)
    t = device_transport(device)
    monkeypatch.setattr(transport.subprocess, "run", child(device, {"down": True, "text": "reset seen"}))
    with pytest.raises(TransportError, match="channel 0 of the device does not open again within 0.02 s"):
        t.far_end_link_reset()


def test_the_child_process_runs_the_link_reset_with_the_software_of_the_far_end(monkeypatch, tmp_path, fast):
    """python -m refsys.far_end_link as the transport starts it, with stand-ins of the far-end packages on PYTHONPATH:
    the child finds the device by its name and resets the link of its port."""
    (tmp_path / "STAR_system").mkdir()
    (tmp_path / "STAR_system" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "STAR_system" / "STAR_enums.py").write_text(textwrap.dedent("""
        class STAR_DEVICE_TYPE:
            STAR_DEVICE_STAR_ULTRA_PCIE = 1
        """), encoding="utf-8")
    (tmp_path / "STAR_system" / "STAR_system.py").write_text(textwrap.dedent("""
        class _Device:
            deviceID = 65536
            def getDeviceName(self):
                return "STAR-Ultra 1"
        class STARSystem:
            def getDeviceListForType(self, kind):
                return [_Device()]
        """), encoding="utf-8")
    (tmp_path / "STAR_Ultra").mkdir()
    (tmp_path / "STAR_Ultra" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "STAR_Ultra" / "interface.py").write_text(textwrap.dedent("""
        class StarUltraPCIeInterface:
            reset = False
            def enableLinkReset(self, device, port):
                assert (device, port) == (65536, 2)
                self.reset = True
            def disableLinkReset(self, device, port):
                self.reset = False
            def getLinkStatus(self, device, port):
                return 0 if self.reset else 4 << 8 | 1
            @staticmethod
            def isLinkReady(status):
                return bool(status & 1)
            @staticmethod
            def getActiveLanesCount(status):
                return status >> 8
        """), encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    device = Device()
    t = device_transport(device)
    result = t.far_end_link_reset(hold=0.001, timeout=1.0)
    assert result.down and result.text.startswith("SpaceFibre port 2 of the device: 4 active lanes before the link "
                                                   "reset, 0 and link not ready while it was held for 0.001 s")
    assert device.events[0] == ("close", 0) and device.events[-1] == ("open", 0)


def test_the_child_reports_a_device_that_is_not_there(monkeypatch, capsys, far_end):
    far_end(Link())
    system = types.ModuleType("STAR_system")
    enums = types.ModuleType("STAR_system.STAR_enums")
    enums.STAR_DEVICE_TYPE = types.SimpleNamespace(STAR_DEVICE_STAR_ULTRA_PCIE=1)
    star = types.ModuleType("STAR_system.STAR_system")
    star.STARSystem = lambda: types.SimpleNamespace(getDeviceListForType=lambda kind: [])
    system.STAR_enums = enums
    for name, module in (("STAR_system", system), ("STAR_system.STAR_enums", enums), ("STAR_system.STAR_system", star)):
        monkeypatch.setitem(sys.modules, name, module)
    assert far_end_link.main(["STAR-Ultra 9", "1", "0.1", "5"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "device 'STAR-Ultra 9' not found; available: none"}


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
