# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Software model of spfi_compress as seen from the far end of its link, for the tests of the hardware test and the
stimuli of the system simulation.

The model of the link, the RMAP node, the Space Packet node and the ECC monitor is that of sw/refsys/sysmodel.py,
which lists the documents of the pinned cores that it follows. Every channel of spfi_compress gives its chunker the
compressed images of its OpenCompact channel: the model of the channel (refsys.ocp.Channel, after the specifications
of ocp_channel and ocp_input_adapter) takes the SDUs with the end mark of their User Application value and their Data
Loss Indicator, and the reference model of OpenCompact compresses every complete image and gives the compressed image
of an aborted one. The chunker cuts every compressed image into chunks of ChunkBeats_g beats, the last one the rest
(Open Logic documentation of olo_base_chunker), and the Space Packet node sends every chunk as one Space Packet. The
status interface of every channel reports the configuration of its generics (OpenCompact register map) and counts as
the model of the channel does.
"""

from collections import deque

import hardware_test as ht
from refsys import ocp, sysmodel
from refsys.composer import SystemFile
from refsys.sysmodel import SPECIFIED
from refsys.transport import TransportError

__all__ = ["SPECIFIED", "SystemModel"]


class SystemModel(sysmodel.SystemModel):
    """spfi_compress as seen from the far end of its link (system: by default from the Composer file). corrupt: every
    chunk leaves with one bit of its last byte inverted."""

    def __init__(self, setup: "ht.Setup", system: "ht.System | None" = None, system_file: SystemFile | None = None,
                 corrupt: bool = False, first_word: tuple[bool, bool] = SPECIFIED):
        system = system or ht.System()
        super().__init__(system, system_file or SystemFile(ht.SYSTEM_FILE), setup, corrupt, first_word)
        self.channels = {c.port: ocp.Channel(c.cfg, c.marks, ocp.runtime_config(c.generics)) for c in system.channels}
        # the registers of the runtime configuration of every compressor (OpenCompact register map), by address
        self.runtime = {}
        for c in system.channels:
            base = system.base(c.compressor)
            self.regs[base + ht.OCP.ID] = ht.OCP.REGMAP_ID
            for name, value in ocp.mib_words(ht.OCP, c.generics).items():
                self.regs[base + getattr(ht.OCP, name)] = value
            registers = ocp.RuntimeRegisters(ht.OCP, c.generics, self.channels[c.port])
            self.runtime |= {base + getattr(ht.OCP, name): (registers, name) for name in ocp.CONFIG_REGISTERS}
        self.pending: deque[bytes] = deque()

    def _read(self, address):
        if address in self.runtime:
            registers, name = self.runtime[address]
            return registers.read(name)
        return super()._read(address)

    def _write(self, address, value):
        if address in self.runtime:
            registers, name = self.runtime[address]
            registers.write(name, value)
        else:
            super()._write(address, value)

    def process(self, channel, data, user_application, loss):
        model = self.channels[channel.port]
        before = vars(model.counts).copy()
        user = (user_application << ht.USER_APPLICATION_SHIFT) | (ht.DATA_LOSS if loss else 0)
        outputs = model.sdu(data, user)
        base = self.system.base(channel.compressor)
        for name, attr in ocp.COUNTERS.items():
            self._count(base + getattr(ht.OCP, name), getattr(model.counts, attr) - before[attr])
        # the values of the last image that started
        registers, _ = self.runtime[base + ht.OCP.CONTROL]
        for name, value in registers.taken().items():
            self.regs[base + getattr(ht.OCP, name)] = value
        return [self._image(channel, o) for o in outputs]

    @staticmethod
    def _image(channel, output: ocp.Output) -> bytes:
        cfg = output.cfg
        if output.complete:
            return ocp.compress(cfg, ocp.from_input_order(cfg, list(output.samples)))
        aborted = ocp.aborted_image(cfg, list(output.samples))
        if aborted is None:
            raise NotImplementedError("the model of OpenCompact gives no aborted image (compress_aborted)")
        return aborted

    def send(self, channel, data):
        parts = ht.chunks_of(data, channel.chunk_bytes)
        if self.corrupt:
            parts = [p[:-1] + bytes([p[-1] ^ 1]) for p in parts]
        return parts

    def exchange(self, tx_channel, packets, rx_channel, count, timeout=5.0):
        """As the base model, but the packets that the request does not take wait for the next receive."""
        if tx_channel == self.setup.rmap_channel:
            return super().exchange(tx_channel, packets, rx_channel, count, timeout)
        assert tx_channel == rx_channel == self.setup.spp_channel
        self.pending.extend(o for p in packets for o in self._spp(p))
        if len(self.pending) < count:
            raise TransportError(f"{len(self.pending)} of {count} packets")
        return [self.pending.popleft() for _ in range(count)]

    def receive(self, channel, timeout):
        if channel == self.setup.spp_channel and self.pending:
            return self.pending.popleft()
        return None
