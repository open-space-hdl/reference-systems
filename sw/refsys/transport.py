"""Transports: the equipment that carries packets between a test and a system.

A transport has numbered channels; what a channel reaches (a virtual channel of a SpaceFibre link, a SpaceWire
port) is a property of the equipment and of the test set-up. Packets are complete link packets ended with an EOP. A
path address is part of the packet: its leading data characters, which the routing switches discard one by one
(ECSS-E-ST-50-12C Rev.1 5.6.8.3). A transport therefore sends the bytes of a packet as they are, path address
included, and returns received packets as they arrive, with the path addresses already used by the routing
switches removed.
"""


class TransportError(Exception):
    """A transfer that did not complete."""


# Steps of the operator at the far end that a test case asks for, with the text shown to the operator
FAR_END_LINK_RESET = "far-end link reset"
OPERATOR_STEPS = {FAR_END_LINK_RESET: "Reset the SpaceFibre link at the port of the test equipment (a link reset of "
                                      "that port, nothing else), then press Enter."}


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

    def begin(self, name: str) -> None:
        """Marks the start of a test case; a simulation records the time at which it starts."""

    def operator(self, step: str) -> None:
        """A step of the operator at the far end (OPERATOR_STEPS): shown, and the test waits for Enter. A simulation
        does what the operator does with the far end of its bench."""
        input(f"OPERATOR: {OPERATOR_STEPS[step]} ")

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def far_end_software() -> str:
    """Imports the Python package of the far-end software that StarSystemTransport uses (--check-environment);
    raises ImportError when it is not installed or does not import. The messages do not name the package."""
    try:
        import STAR_system  # noqa: F401
    except ImportError as err:
        missing = isinstance(err, ModuleNotFoundError) and err.name == "STAR_system"
        raise ImportError("not installed" if missing else
                          f"installed, but its import fails ({type(err).__name__})") from None
    return "found"


class StarSystemTransport(Transport):
    """STAR-Dundee devices through the STAR-System software, which must be installed on the test computer with its
    Python package. A channel number is a channel of the device."""

    def __init__(self, device_name: str):
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
        self._channels = devices[device_name].getChannels()
        for channel in self._channels:
            channel.openChannelToDevice(enums.STAR_CHANNEL_DIRECTION.INOUT)

    def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
        eop = self._enums.STAR_EOP_TYPE.STAR_EOP_TYPE_EOP
        receive = self._receive(count, receivePackets=True)
        # the whole packet is the content of the stream item, path address included (no separate address)
        transmit = self._transmit([self._stream_item.createPacket([], list(p), eop) for p in packets])
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
        return [bytes(item.getPacketData()) for item in receive.getTransferItemList()]

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

    @staticmethod
    def _cancel(operations) -> None:
        # a pending operation would take the next packet of its channel
        for op in operations:
            try:
                op.cancelTransferOperation()
            except Exception:  # noqa: BLE001 - best effort
                pass
