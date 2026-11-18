"""Transports: the equipment that carries packets between a test and a system.

A transport has numbered channels; what a channel reaches (a virtual channel of a SpaceFibre link, a SpaceWire
port) is a property of the equipment and of the test set-up. Packets are complete link packets ended with an EOP. A
path address is part of the packet: its leading data characters, which the routing switches discard one by one
(ECSS-E-ST-50-12C Rev.1 5.6.8.3). A transport therefore sends the bytes of a packet as they are, path address
included, and returns received packets as they arrive, with the path addresses already used by the routing
switches removed.

A transport also resets the link at the far end on request (far_end_link_reset): the hardware test runs without an
operator.

A transfer whose duration counts is prepared first and run then (prepare, run_prepared): everything that does not need
the far end (the operations of the device, the packets as stream items) is built before the timed part, and the
received packets are converted after it, so that the time is that of the transfer alone (Timing).
"""

import time
from dataclasses import dataclass


class TransportError(Exception):
    """A transfer that did not complete."""


# The far-end link reset of the test equipment: how long its SpaceFibre port is held in link reset, and how long the
# link of the port may take to be ready again with its lanes afterwards
FAR_END_RESET_HOLD = 0.1
FAR_END_LINK_TIMEOUT = 5.0


@dataclass(frozen=True)
class Timing:
    """The time of a transfer that run_prepared ran: wire_s from the submission of its first operation to the arrival
    of its last packet; convert_s, the conversion of the received packets on the host after it; items, the packets
    received; device, whether the far end is the device (False: the far end is a model on the host, and wire_s is the
    time of the model)."""

    wire_s: float
    convert_s: float
    items: int
    device: bool


@dataclass(frozen=True)
class FarEndReset:
    """What a far-end link reset shows at the far end: down, the link of the far end was not ready while it was held in
    reset (the reset reached the link); text, what the far end saw, for the records."""

    down: bool
    text: str


class Transport:
    """Interface of a transport."""

    def request(self, channel: int, packet: bytes, timeout: float = 5.0) -> bytes:
        """Sends one packet and returns the next packet received on the same channel."""
        return self.exchange(channel, [packet], channel, 1, timeout)[0]

    def exchange(self, tx_channel: int, packets: list[bytes], rx_channel: int, count: int,
                 timeout: float = 5.0) -> list[bytes]:
        """Sends packets on one channel and returns the next count packets received on another (or the same)
        channel; reception is armed before the first packet leaves."""
        raise NotImplementedError

    def receive(self, channel: int, timeout: float) -> bytes | None:
        """Returns the next packet received on a channel, or None when none arrives within timeout."""
        raise NotImplementedError

    def transmit(self, channel: int, packets: list[bytes], timeout: float = 5.0) -> None:
        """Sends packets on a channel without waiting for a packet in return (a packet that the system discards)."""
        self.exchange(channel, packets, channel, 0, timeout)

    def prepare(self, tx_channel: int, packets: list[bytes], rx_channel: int, count: int):
        """Everything of an exchange that does not need the far end, done before it is timed (run_prepared). A model
        keeps the arguments; a device builds its transfer operations here."""
        return tx_channel, list(packets), rx_channel, count

    def run_prepared(self, prepared, timeout: float = 5.0) -> tuple[list[bytes], Timing]:
        """Runs a prepared exchange: the received packets and the timing of the transfer. A model times its exchange
        (Timing.device False)."""
        tx_channel, packets, rx_channel, count = prepared
        start = time.monotonic()
        received = self.exchange(tx_channel, packets, rx_channel, count, timeout)
        return received, Timing(time.monotonic() - start, 0.0, len(received), False)

    def begin(self, name: str) -> None:
        """Marks the start of a test case; a simulation records the time at which it starts."""

    def far_end_link_reset(self) -> FarEndReset:
        """Resets the link at the far end, as the far end itself does it, and returns when the link of the far end is
        initialised again; the system sees a far-end link reset. A simulation does it with the far end of its
        bench."""
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def far_end_software() -> str:
    """Imports the Python packages of the far-end software that StarSystemTransport uses (--check-environment): the
    transfers and the link control of the SpaceFibre port (far_end_link_reset); raises ImportError when one is not
    installed, does not import or does not load its library. The messages do not name the packages."""
    try:
        import STAR_system  # noqa: F401
    except ImportError as err:
        missing = isinstance(err, ModuleNotFoundError) and err.name == "STAR_system"
        raise ImportError("not installed" if missing else
                          f"installed, but its import fails ({type(err).__name__})") from None
    try:
        from STAR_Ultra import interface
    except ImportError as err:
        raise ImportError("installed, without the link control of the SpaceFibre port "
                          f"({type(err).__name__})") from None
    if getattr(interface, "star_ultra_lib", None) is None:
        raise ImportError("installed, but the library of the link control of the SpaceFibre port does not load")
    return "found"


class StarSystemTransport(Transport):
    """STAR-Dundee devices through the STAR-System software, which must be installed on the test computer with its
    Python packages. A channel number is a channel of the device; spfi_port, the SpaceFibre port of the device that is
    cabled to the system (1 or 2), whose link far_end_link_reset resets through the STAR-Ultra PCIe Interface
    library."""

    def __init__(self, device_name: str, spfi_port: int = 1):
        from STAR_system import STAR_enums as enums
        from STAR_system.STAR_system import STARSystem
        from STAR_system.stream_item import StreamItem
        from STAR_system.transfer_operations import ReceiveOperation, TransmitOperation

        self._enums = enums
        self._stream_item = StreamItem
        self._receive = ReceiveOperation
        self._transmit = TransmitOperation
        devices = {d.getDeviceName(): d
                   for d in STARSystem().getDeviceListForType(enums.STAR_DEVICE_TYPE.STAR_DEVICE_STAR_ULTRA_PCIE)}
        if device_name not in devices:
            raise TransportError(f"device {device_name!r} not found; available: {', '.join(devices) or 'none'}")
        self._device_id = devices[device_name].deviceID
        self._spfi_port = spfi_port
        self._channels = devices[device_name].getChannels()
        for channel in self._channels:
            channel.openChannelToDevice(enums.STAR_CHANNEL_DIRECTION.INOUT)

    def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
        return self.run_prepared(self.prepare(tx_channel, packets, rx_channel, count), timeout)[0]

    def prepare(self, tx_channel, packets, rx_channel, count):
        """The transfer operations of an exchange: one transmit operation with every packet as a stream item (the whole
        packet is the content of the item, path address included, no separate address) and one receive operation for
        count packets."""
        eop = self._enums.STAR_EOP_TYPE.STAR_EOP_TYPE_EOP
        transmit = self._transmit([self._stream_item.createPacket([], list(p), eop) for p in packets])
        return tx_channel, transmit, rx_channel, self._receive(count, receivePackets=True)

    def run_prepared(self, prepared, timeout=5.0):
        """Submits the receive operation, then the transmit operation, and waits for both. wire_s runs from the first
        submission until the receive operation completes, that is until its last packet has arrived (the completion of
        a transmit operation only means that the device took its packets); the received packets are converted after
        it."""
        tx_channel, transmit, rx_channel, receive = prepared
        start = time.monotonic()
        try:
            self._channels[rx_channel].submitTransferOperation(receive)
            self._channels[tx_channel].submitTransferOperation(transmit)
            for op, what in ((transmit, "transmit"), (receive, "receive")):
                status = op.waitOnTransferOperationCompletion(timeout=int(timeout * 1000))
                if status != self._enums.STAR_TRANSFER_STATUS.STAR_TRANSFER_STATUS_COMPLETE:
                    raise TransportError(f"{what} on channel {rx_channel if op is receive else tx_channel} did not "
                                         f"complete within {timeout} s ({status})")
        except BaseException:
            self._cancel([receive, transmit])
            raise
        wire = time.monotonic() - start
        received = [bytes(item.getPacketData()) for item in receive.getTransferItemList()]
        return received, Timing(wire, time.monotonic() - start - wire, len(received), True)

    def transmit(self, channel, packets, timeout=5.0):
        eop = self._enums.STAR_EOP_TYPE.STAR_EOP_TYPE_EOP
        transmit = self._transmit([self._stream_item.createPacket([], list(p), eop) for p in packets])
        try:
            self._channels[channel].submitTransferOperation(transmit)
            status = transmit.waitOnTransferOperationCompletion(timeout=int(timeout * 1000))
            if status != self._enums.STAR_TRANSFER_STATUS.STAR_TRANSFER_STATUS_COMPLETE:
                raise TransportError(f"transmit on channel {channel} did not complete within {timeout} s ({status})")
        except BaseException:
            self._cancel([transmit])
            raise

    def receive(self, channel, timeout):
        receive = self._receive(1, receivePackets=True)
        try:
            self._channels[channel].submitTransferOperation(receive)
            status = receive.waitOnTransferOperationCompletion(timeout=int(timeout * 1000))
        except BaseException:
            self._cancel([receive])
            raise
        if status != self._enums.STAR_TRANSFER_STATUS.STAR_TRANSFER_STATUS_COMPLETE:
            self._cancel([receive])
            return None
        return bytes(receive.getTransferItemList()[0].getPacketData())

    def far_end_link_reset(self, hold: float = FAR_END_RESET_HOLD,
                           timeout: float = FAR_END_LINK_TIMEOUT) -> FarEndReset:
        """The link reset of the SpaceFibre port of the device: LINK_RESET held for hold seconds, then released, and
        the link polled until it is ready with at least the active lanes it had before, within timeout seconds. The
        link state read while LINK_RESET is held shows whether the reset reached the link (FarEndReset.down)."""
        from STAR_Ultra.interface import StarUltraPCIeInterface

        link = StarUltraPCIeInterface()
        device, port = self._device_id, self._spfi_port
        lanes = link.getActiveLanesCount(link.getLinkStatus(device, port))
        link.enableLinkReset(device, port)
        try:
            time.sleep(hold)
            held = link.getLinkStatus(device, port)
        finally:
            link.disableLinkReset(device, port)
        start = time.monotonic()
        while True:
            status = link.getLinkStatus(device, port)
            if link.isLinkReady(status) and link.getActiveLanesCount(status) >= lanes:
                break
            if time.monotonic() - start > timeout:
                raise TransportError(f"the link of SpaceFibre port {port} of the device is not ready with {lanes} "
                                     f"lanes {timeout} s after its link reset (link status 0x{status:08X})")
            time.sleep(0.005)
        down = not link.isLinkReady(held)
        return FarEndReset(down, f"SpaceFibre port {port} of the device: {lanes} active lanes before the link reset, "
                                 f"{link.getActiveLanesCount(held)} and link {'not ' if down else ''}ready while it "
                                 f"was held for {hold:.3f} s, ready with {link.getActiveLanesCount(status)} lanes "
                                 f"{time.monotonic() - start:.3f} s after its release")

    @staticmethod
    def _cancel(operations) -> None:
        # a pending operation would take the next packet of its channel
        for op in operations:
            try:
                op.cancelTransferOperation()
            except Exception:  # noqa: BLE001 - best effort
                pass
