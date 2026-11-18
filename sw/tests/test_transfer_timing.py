# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Tests of the timed transfer of the device (StarSystemTransport.prepare and run_prepared in sw/refsys/transport.py)
and of the rate of TC-RS-HW-09 (systest.rate_text), with a stand-in for the transfer operations of the far-end
software: the stream items are built before the timed part, the received packets are converted after it, and the
time of the transfer runs from the first submission until the receive operation completes."""

import time
from types import SimpleNamespace

import pytest

from refsys import systest
from refsys.transport import StarSystemTransport, Timing, Transport

COMPLETE = "complete"


class Operation:
    """Stand-in for a transfer operation: completes after the time of the wire; a received item converts slowly."""

    wire = 0.05

    def __init__(self, items=None, count=0, receivePackets=True):
        self.items, self.count, self.submitted = items or [], count, None

    def waitOnTransferOperationCompletion(self, timeout):
        time.sleep(max(0.0, self.submitted + self.wire - time.monotonic()))
        return COMPLETE

    def getTransferItemList(self):
        return [SimpleNamespace(getPacketData=lambda n=n: (time.sleep(0.01), [n, n])[1]) for n in range(self.count)]

    def cancelTransferOperation(self):
        pass


class Channel:
    def submitTransferOperation(self, op):
        op.submitted = time.monotonic()


@pytest.fixture
def device():
    built = []
    d = StarSystemTransport.__new__(StarSystemTransport)
    d._enums = SimpleNamespace(STAR_EOP_TYPE=SimpleNamespace(STAR_EOP_TYPE_EOP="EOP"),
                               STAR_TRANSFER_STATUS=SimpleNamespace(STAR_TRANSFER_STATUS_COMPLETE=COMPLETE))
    d._stream_item = SimpleNamespace(createPacket=lambda a, data, eop: built.append(bytes(data)) or data)
    d._transmit = lambda items: Operation(items=items)
    d._receive = lambda count, receivePackets: Operation(count=count)
    d._channels = [Channel(), Channel()]
    d.built = built
    return d


def test_the_stream_items_are_built_before_and_the_packets_converted_after_the_timed_part(device):
    prepared = device.prepare(1, [b"\x01\x02", b"\x03"], 1, 4)
    assert device.built == [b"\x01\x02", b"\x03"]            # built by prepare, before the timed part
    received, timing = device.run_prepared(prepared, timeout=1.0)
    assert [bytes(p) for p in received] == [bytes([n, n]) for n in range(4)]
    assert timing.device and timing.items == 4
    assert Operation.wire <= timing.wire_s < Operation.wire + 0.03     # the wire, not the conversion
    assert timing.convert_s >= 4 * 0.01


def test_an_exchange_is_a_prepared_transfer(device):
    assert [bytes(p) for p in device.exchange(1, [b"\x05"], 1, 1, timeout=1.0)] == [b"\x00\x00"]


def test_a_model_times_its_exchange_and_says_so():
    class Model(Transport):
        def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
            return [b"\x01"] * count

    received, timing = Model().run_prepared(Model().prepare(1, [b"\x00"], 1, 2))
    assert received == [b"\x01", b"\x01"] and not timing.device and timing.items == 2


def test_the_rate_of_the_device_counts_the_samples_and_the_link_packets_over_the_transfer():
    transfer = {"timing": Timing(0.5, 0.25, 98, True), "sent": 500_000_000, "received": 125_000_000}
    text = systest.rate_text(transfer, 250_000_000, 100_000_000, 32)
    assert text == ("32 images, 250000000 bytes of samples, 100000000 bytes compressed; transfer 0.500 s from the "
                    "first submission to the last packet received: 4.000 Gbit/s of samples, 8.000 Gbit/s of link "
                    "packets to the system and 2.000 Gbit/s from it. Not timed: the images and packets built before, "
                    "the conversion of the 98 packets received (0.250 s) and the checks after")


def test_against_the_model_the_rate_is_no_rate_of_the_system():
    transfer = {"timing": Timing(3.0, 0.0, 98, False), "sent": 1, "received": 1}
    assert systest.rate_text(transfer, 10, 5, 2).endswith(": the far end is the system model, whose time is no rate "
                                                          "of the system")
