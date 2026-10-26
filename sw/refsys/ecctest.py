# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Steps of TC-RS-HW-14: memory errors injected into the SECDED-protected buffers of the SpaceFibre port, the RMAP
target and the Space Packet node through their ECC_INJECT registers, during traffic. An injection is latched and
applied to the next word written into its buffer (Open Logic FT, principles of the ft entities); the traffic of the
test case writes every buffer.

Single errors (singles), all at once before one round of traffic: they are corrected, the data stay intact, and the
counter of the monitor channel of every buffer counts them:

- SpaceFibre port (OpenFibre user guide, ECC; register map ECC_INJECT): channels 0 (output VC buffers, one per VC),
  1 (retry buffer, whose scrubber may count the same error again), 2 (frame buffer), 3 (input VC buffers, one per
  VC), 6 and 7 (transmit and receive row crossings) and 8 (the write FIFO of the quality of service registers,
  written through VC_IDLE_LIMIT). Channels 2, 6, 7 and 8 count exactly 1; channels 0 and 3, one buffer per VC whose
  events may arrive too close together to count twice, 1 or 2; channel 1 at least 1. Channels 4 and 5 (broadcast)
  are not exercised: the system ties the broadcast port off.
- RMAP target (OpenRMAP user guide, section 6): channels 0 (write buffer), 1 (write data of the AXI master) and 2
  (read data of the AXI master), each exactly 1.
- Space Packet node (OpenSPP register map, ECC_INJECT; ECC_SELECT for the monitor channels): the receive data buffer
  and descriptor (monitor channels 4 and 5) and the data buffer and descriptor of send port p (monitor channels 0
  and 1) exactly 1 each; the receive and the send APID table at least 1 on the read or the scrubber channel of the
  table (6 or 7, 2 or 3), since the scrubber may read the entry first.

Double errors (doubles), each with an outcome that the test sees:

- SpaceFibre port channel 6: the CRC-16 of the next data frame on its lane is inverted, the far end discards the
  frame and the frame is sent again (CORE-ED-02); channel 7: the row becomes a receive error and the far end sends
  the frame again. Each counts one uncorrectable error; the link stays up, the data intact; the retries and the
  error counters of the data link layer that the retry moves are recorded.
- RMAP target channel 0: the next verified write ends with the status 1 (TG-EX-06).
- Space Packet node, receive descriptor: the next Space Packet is discarded with the code 0x1B (a sacrificial packet
  of an APID that no channel uses).

Not exercised, with the reason in the coverage table: the doubles of the SpaceFibre port on channels 0 to 3 (a link
reset, packets that end with an EEP), of the RMAP target on channel 2 and of the send data buffers of the Space
Packet node (replies and packets that end with an EEP, which the transport does not report), of the send
descriptors and tables (an output chunk lost) and of the receive data buffer and table.
"""

from refsys import errorstate, rmap, systest
from refsys.spp import cptp_encode
from refsys.spptest import space_packet
from refsys.systest import OFB, OMAP, SPP, Registers, check

OFB_SINGLES = (0, 1, 2, 3, 6, 7, 8)
OFB_EXACT = (2, 6, 7, 8)                      # channels that count exactly 1
OFB_PER_VC = (0, 3)                           # one buffer per VC: 1 to NumVc_g
OFB_DOUBLES = (6, 7)
SACRIFICE_APID = 0x021                        # the sacrificial Space Packet of the receive descriptor double


def sec(regmap, value: int = 1) -> int:
    return regmap.field("ECC_COUNT", "SEC" if regmap is not OFB else "SEC_COUNT", value)


def ded(regmap, value: int = 1) -> int:
    return regmap.field("ECC_COUNT", "DED" if regmap is not OFB else "DED_COUNT", value)


def spp_inject(channel: int, rx: bool, double: bool = False) -> int:
    return (SPP.field("ECC_INJECT", "CHANNEL", channel) | SPP.field("ECC_INJECT", "RX", int(rx))
            | SPP.field("ECC_INJECT", "DOUBLE" if double else "SINGLE", 1))


def inject_singles(regs: Registers, ctx, port: int) -> None:
    """Injects the single errors (module documentation); port: the send port of the Space Packet node whose buffers
    take one."""
    s = ctx.system
    ofb, omap, spp = s.base(s.port), s.base(s.rmap), s.base(s.node)
    for ch in OFB_SINGLES:
        regs.write(ofb + OFB.ECC_INJECT, OFB.field("ECC_INJECT", "CHANNEL", ch))
    # the write FIFO of the quality of service registers takes the next write of VC_IDLE_LIMIT
    regs.write(ofb + OFB.VC_IDLE_LIMIT, systest.port_reset_values(ctx)["VC_IDLE_LIMIT"])
    for ch in range(3):
        regs.write(omap + OMAP.ECC_INJECT, OMAP.field("ECC_INJECT", "CHANNEL", ch) | OMAP.field("ECC_INJECT", "SINGLE", 1))
    sends = len(s.channels)
    for channel, rx in ((0, True), (1, True), (2, True), (2 * port, False), (2 * port + 1, False), (2 * sends, False)):
        regs.write(spp + SPP.ECC_INJECT, spp_inject(channel, rx))


def check_singles(regs: Registers, ctx) -> None:
    """After the traffic: every injected single counted (module documentation), nothing else; recorded and cleared."""
    s = ctx.system
    vcs = s.num_vcs
    expected = {f"{s.port}.ECC_COUNT({ch})": sec(OFB) for ch in OFB_EXACT}
    expected |= {f"{s.rmap}.ECC_COUNT({ch})": sec(OMAP) for ch in range(3)}
    expected |= {f"{s.node}.ECC_COUNT({ch})": sec(SPP) for ch in (0, 1, 4, 5)}
    expected[f"{s.port}.ECC_STATUS"] = OFB.field("ECC_STATUS", "SEC", 1)
    expected[f"{s.rmap}.EVENTS"] = OMAP.field("EVENTS", "ECC_SEC", 1)
    expected[f"{s.node}.EVENTS"] = SPP.field("EVENTS", "ECC_SEC", 1)
    sec_any = OFB.field("ECC_COUNT", "SEC_COUNT", OFB.ECC_COUNT_SEC_COUNT_MASK)
    spp_any = SPP.field("ECC_COUNT", "SEC", SPP.ECC_COUNT_SEC_MASK)
    allowed = {f"{s.port}.ECC_COUNT({ch})": sec_any for ch in (0, 1, 3)}
    allowed |= {f"{s.node}.ECC_COUNT({ch})": spp_any for ch in (2, 3, 6, 7)}
    state = systest.caused(regs, ctx, "TC-RS-HW-14 single errors", expected=expected, allowed=allowed)
    count = {name: value & sec_any for name, value in state.items() if name.startswith(f"{s.port}.ECC_COUNT")}
    for ch in OFB_PER_VC:
        check(1 <= count[f"{s.port}.ECC_COUNT({ch})"] <= vcs,
              f"{s.port} channel {ch}: {count[f'{s.port}.ECC_COUNT({ch})']} corrected errors, expected 1 to {vcs}")
    check(count[f"{s.port}.ECC_COUNT(1)"] >= 1, f"{s.port} channel 1: no corrected error")
    for table in ((2, 3), (6, 7)):
        total = sum(state[f"{s.node}.ECC_COUNT({ch})"] & spp_any for ch in table)
        check(total >= 1, f"{s.node} APID table of monitor channels {table}: no corrected error")


def inject_ofb_double(regs: Registers, ctx, channel: int) -> None:
    s = ctx.system
    regs.write(s.base(s.port) + OFB.ECC_INJECT, OFB.field("ECC_INJECT", "CHANNEL", channel)
               | OFB.field("ECC_INJECT", "DOUBLE", 1))


def rmap_double(regs: Registers, ctx) -> None:
    """The double error in the write buffer of the RMAP target: the next verified write (of the scratch register
    WIN_BASE of the last window) ends with the status 1; the register is written again afterwards."""
    s = ctx.system
    omap = s.base(s.rmap)
    scratch = omap + OMAP.WIN_BASE(s.rmap_windows - 1)
    regs.write(omap + OMAP.ECC_INJECT, OMAP.field("ECC_INJECT", "CHANNEL", 0) | OMAP.field("ECC_INJECT", "DOUBLE", 1))
    tid = regs.next_tid()
    reply = regs.command(rmap.write_command(regs.target, scratch, (0x5A5A5A5A).to_bytes(4, "little"), tid), tid,
                         read=False)
    check(reply.status == 1, f"verified write after the double error in the write buffer of {s.rmap}: status "
          f"{reply.status} ({rmap.status_text(reply.status)}), expected 1")
    regs.write(scratch, 0)
    check(regs.read(scratch) == 0, f"{s.rmap} WIN_BASE of the last window is not restored")


def spp_double(regs: Registers, ctx) -> None:
    """The double error in the receive descriptor of the Space Packet node: the next Space Packet, a sacrificial one,
    is discarded with the code 0x1B."""
    s, setup = ctx.system, ctx.setup
    node = s.base(s.node)
    regs.write(node + SPP.ECC_INJECT, spp_inject(1, True, double=True))
    regs.transport.transmit(setup.spp_channel, [cptp_encode(space_packet(SACRIFICE_APID, bytes(16)),
                                                            setup.system_tla)])
    regs.wait(node + SPP.LAST_ERROR, 0xFFFFFFFF, SPP.field("LAST_ERROR", "CODE", 0x1B))


def check_doubles(regs: Registers, ctx) -> None:
    """After the doubles and the traffic: one uncorrectable error on every channel with a double, the outcomes
    counted, the retries and the error counters of the data link layer recorded; cleared."""
    s = ctx.system
    expected = {f"{s.port}.ECC_COUNT({ch})": ded(OFB) for ch in OFB_DOUBLES}
    expected[f"{s.port}.ECC_STATUS"] = sum(OFB.field("ECC_STATUS", "DED", 1 << ch) for ch in OFB_DOUBLES)
    expected[f"{s.rmap}.ECC_COUNT(0)"] = ded(OMAP)
    expected[f"{s.rmap}.ECC_STATUS"] = OMAP.field("ECC_STATUS", "DED", 1)
    expected[f"{s.rmap}.TGT_CNT_ERR1"] = OMAP.field("TGT_CNT_ERR1", "GENERAL", 1)
    expected[f"{s.rmap}.EVENTS"] = OMAP.field("EVENTS", "TGT_ERROR", 1) | OMAP.field("EVENTS", "ECC_DED", 1)
    expected[f"{s.node}.ECC_COUNT(5)"] = ded(SPP)
    expected[f"{s.node}.ECC_STATUS"] = SPP.field("ECC_STATUS", "DED", 1 << 5)
    expected[f"{s.node}.ERR_RX6"] = SPP.field("ERR_RX6", "BUFFER", 1)
    expected[f"{s.node}.EVENTS"] = SPP.field("EVENTS", "RX_DISCARD", 1) | SPP.field("EVENTS", "ECC_DED", 1)
    link = sum(OFB.field("DL_ERRORS", f, 1) for f in ("CRC16", "CRC8", "FRAME", "SEQUENCE"))
    allowed = {f"{s.port}.DL_ERRORS": link}
    allowed |= {f"{s.port}.{r}": errorstate.ALL for r in ("DL_RETRIES", "DL_CRC16_COUNT", "DL_CRC8_COUNT",
                                                          "DL_FRAME_COUNT", "DL_SEQ_COUNT")}
    allowed |= {f"{s.port}.LANE_STATUS({lane}).RXERR_COUNT": errorstate.ALL for lane in range(s.lanes)}
    systest.caused(regs, ctx, "TC-RS-HW-14 double errors", expected=expected, allowed=allowed)
