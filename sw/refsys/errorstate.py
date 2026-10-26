# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""The error and memory error registers of the blocks of a reference system: read them all, check that none
counted, record what they hold and clear them.

The test of a system takes a baseline right after the link came up (TC-RS-HW-01: read, record, clear) and checks at
the end of the run that nothing counted since (TC-RS-HW-17). A test case that provokes errors on purpose checks
before it clears that only the registers it targets moved (allowed): every other error register must still be
0. The registers and how they clear come from the register maps of the cores:

- SpaceFibre port (OpenFibre register map): DL_ERRORS and the VC flags are write-one-to-clear, the counters of the
  data link layer, the multi-lane layer and the network layer are cleared by any write; LANE_EVENTS (W1C) and
  LANE_TIMEOUT_COUNT (any write) of every lane; the memory error counters of the channels through ECC_SELECT and
  ECC_COUNT, cleared all at once by a write of ECC_STATUS. RXERR_COUNT of LANE_STATUS is a leaky counter that no
  write clears (it counts down by one every 16384 words): it may not exceed its baseline. VC_BW_OVER and
  VC_BW_UNDER (bandwidth of the virtual channels against their settings) and DL_ERRORS.BC_DISCARD (broadcasts of the
  far end, which the system does not take) are recorded, they are no errors of the system.
- RMAP target (OpenRMAP register map): the error counters TGT_CNT_HDR and TGT_CNT_ERR1 to ERR6 and PKT_CNT (any
  write clears), the error bits of EVENTS (W1C) and the memory error counters as for the SpaceFibre port.
- Space Packet node (OpenSPP register map): ERR_TX1 to ERR_TX4 and ERR_RX1 to ERR_RX6 (any write clears), the
  error bits of EVENTS and the overrun bits of TABLE_STATUS (W1C) and the memory error counters as above.
- ECC monitor (Open Logic FT, olo_ft_ecc_monitor_axi): the counters of every channel and IRQ_STATUS; CTRL.CLR_ALL
  clears the counters, IRQ_STATUS is write-one-to-clear.
- A system adds the error counters of its own blocks (Register).
"""

from dataclasses import dataclass

ALL = 0xFFFFFFFF


@dataclass(frozen=True)
class Register:
    """An error register. errors: the bits that count as an error (the others are only recorded); clear: the value
    whose write clears it (0 for a register that any write clears, the error bits for a write-one-to-clear one), None
    when another register clears it; select: (address of the select register, channel) for a counter read through a
    select register; leaky: a counter that no write clears and that counts down by itself (it may not exceed its
    baseline)."""

    name: str
    address: int
    errors: int = ALL
    clear: int | None = 0
    select: tuple[int, int] | None = None
    leaky: bool = False


@dataclass(frozen=True)
class Clear:
    """A write that clears registers that the list does not clear one by one (ECC_STATUS, CTRL.CLR_ALL): the names
    of the registers it clears."""

    name: str
    address: int
    value: int
    clears: tuple[str, ...] = ()


def ofb_registers(regs, name: str, base: int, lanes: int, vcs: int, ecc_channels: int = 9) -> list:
    """The error registers of a SpaceFibre port (OpenFibre)."""
    vc_bits = (1 << vcs) - 1
    dl_errors = ALL & ~regs.field("DL_ERRORS", "BC_DISCARD", 1) & 0x1FFF
    out = [Register(f"{name}.DL_ERRORS", base + regs.DL_ERRORS, dl_errors, 0x1FFF)]
    out += [Register(f"{name}.{r}", base + getattr(regs, r)) for r in
            ("DL_RETRIES", "DL_CRC16_COUNT", "DL_CRC8_COUNT", "DL_FRAME_COUNT", "DL_SEQ_COUNT")]
    out += [Register(f"{name}.{r}", base + getattr(regs, r), vc_bits, vc_bits) for r in
            ("VC_INPUT_OVERFLOW", "VC_CREDIT_OVERFLOW", "VC_FRAMING_ERROR", "VC_RX_FRAMING_ERROR")]
    out += [Register(f"{name}.{r}", base + getattr(regs, r), 0, vc_bits) for r in ("VC_BW_OVER", "VC_BW_UNDER")]
    out += [Register(f"{name}.{r}", base + getattr(regs, r)) for r in
            ("ML_MISALIGNED", "ML_ALIGN_TIMEOUTS", "NI_RX_EMPTY_COUNT")]
    for lane in range(lanes):
        rxerr = regs.field("LANE_STATUS", "RXERR_COUNT", regs.LANE_STATUS_RXERR_COUNT_MASK)
        out += [Register(f"{name}.LANE_STATUS({lane}).RXERR_COUNT", base + regs.LANE_STATUS(lane), rxerr, None,
                         leaky=True),
                Register(f"{name}.LANE_EVENTS({lane})", base + regs.LANE_EVENTS(lane), 0xFF, 0xFF),
                Register(f"{name}.LANE_REASONS({lane})", base + regs.LANE_REASONS(lane), 0, None),
                Register(f"{name}.LANE_TIMEOUT_COUNT({lane})", base + regs.LANE_TIMEOUT_COUNT(lane))]
    return out + ecc_registers(regs, name, base, ecc_channels)


def ecc_registers(regs, name: str, base: int, channels: int) -> list:
    """ECC_STATUS and the counters of every channel through ECC_SELECT and ECC_COUNT of a core whose ECC_STATUS
    write clears them all (OpenFibre, OpenRMAP, OpenSPP)."""
    out = [Register(f"{name}.ECC_STATUS", base + regs.ECC_STATUS, ALL, None)]
    out += [Register(f"{name}.ECC_COUNT({ch})", base + regs.ECC_COUNT, ALL, None, select=(base + regs.ECC_SELECT, ch))
            for ch in range(channels)]
    return out + [Clear(f"{name}.ECC_STATUS", base + regs.ECC_STATUS, 0, tuple(r.name for r in out))]


def omap_registers(regs, name: str, base: int) -> list:
    """The error registers of an RMAP target (OpenRMAP)."""
    events = sum(regs.field("EVENTS", f, 1) for f in ("TGT_ERROR", "TGT_DISCARD", "ECC_SEC", "ECC_DED",
                                                      "PKT_DISCARD"))
    out = [Register(f"{name}.{r}", base + getattr(regs, r)) for r in
           ("TGT_CNT_HDR", "TGT_CNT_ERR1", "TGT_CNT_ERR2", "TGT_CNT_ERR3", "TGT_CNT_ERR4", "TGT_CNT_ERR5",
            "TGT_CNT_ERR6")]
    out += [Register(f"{name}.PKT_CNT", base + regs.PKT_CNT, regs.field("PKT_CNT", "DISCARDED", 0xFFFF)),
            Register(f"{name}.EVENTS", base + regs.EVENTS, events, events)]
    return out + ecc_registers(regs, name, base, 3)


def spp_registers(regs, name: str, base: int) -> list:
    """The error registers of a Space Packet node (OpenSPP)."""
    events = sum(regs.field("EVENTS", f, 1) for f in ("TX_REJECT", "RX_DISCARD", "ECC_SEC", "ECC_DED"))
    overrun = regs.field("TABLE_STATUS", "TX_OVERRUN", 1) | regs.field("TABLE_STATUS", "RX_OVERRUN", 1)
    out = [Register(f"{name}.{r}", base + getattr(regs, r)) for r in
           ("ERR_TX1", "ERR_TX2", "ERR_TX3", "ERR_TX4", "ERR_RX1", "ERR_RX2", "ERR_RX3", "ERR_RX4", "ERR_RX5",
            "ERR_RX6")]
    out += [Register(f"{name}.EVENTS", base + regs.EVENTS, events, events),
            Register(f"{name}.TABLE_STATUS", base + regs.TABLE_STATUS, overrun, overrun)]
    return out + ecc_registers(regs, name, base, 8)


# olo_ft_ecc_monitor_axi: CTRL (CLR_ALL in bit 0), IRQ_STATUS (W1C), the counters of channel ch at 0x10 + 4 ch
EDAC_CTRL, EDAC_IRQ_STATUS, EDAC_CNT = 0x04, 0x08, 0x10


def edac_registers(name: str, base: int, channels: int) -> list:
    """The counters and IRQ_STATUS of an ECC monitor (Open Logic FT)."""
    out = [Register(f"{name}.IRQ_STATUS", base + EDAC_IRQ_STATUS, 0x3, 0x3)]
    counters = [Register(f"{name}.CNT({ch})", base + EDAC_CNT + 4 * ch, ALL, None) for ch in range(channels)]
    return out + counters + [Clear(f"{name}.CTRL.CLR_ALL", base + EDAC_CTRL, 1, tuple(r.name for r in counters))]


def reads(items: list) -> list[tuple[int, list[Register]]]:
    """The commands that read the registers: (address, registers) of runs of consecutive registers without a select
    register, and one per selected counter, in the order of the list."""
    plain = sorted((r for r in items if isinstance(r, Register) and r.select is None), key=lambda r: r.address)
    runs: list[tuple[int, list[Register]]] = []
    for r in plain:
        if runs and r.address == runs[-1][0] + 4 * len(runs[-1][1]) and len(runs[-1][1]) < 16:
            runs[-1][1].append(r)
        else:
            runs.append((r.address, [r]))
    return runs + [(r.address, [r]) for r in items if isinstance(r, Register) and r.select is not None]


def read_state(regs, items: list) -> dict[str, int]:
    """The value of every error register, by name. The select registers are back at 0, their value of the reset,
    afterwards."""
    state = {}
    selects = []
    for address, run in reads(items):
        if run[0].select:
            select, channel = run[0].select
            regs.write(select, channel)
            selects += [select] if select not in selects else []
        for r, value in zip(run, regs.read_block(address, len(run))):
            state[r.name] = value
    for select in selects:
        regs.write(select, 0)
    return state


def unexpected(items: list, state: dict[str, int], allowed: dict[str, int] | None = None,
               baseline: dict[str, int] | None = None) -> list[str]:
    """The registers whose error bits hold a value that the test case does not allow: allowed gives per name the
    bits that may be set (ALL: any value of the register); a leaky counter may not exceed its baseline."""
    allowed = allowed or {}
    out = []
    for r in items:
        if not isinstance(r, Register):
            continue
        value = state[r.name] & r.errors & ~allowed.get(r.name, 0)
        if r.leaky:
            if value > (baseline or {}).get(r.name, 0) & r.errors:
                out.append(f"{r.name} 0x{state[r.name]:08X}")
        elif value:
            out.append(f"{r.name} 0x{state[r.name]:08X}")
    return out


def nonzero(state: dict[str, int]) -> str:
    """The registers that hold a value, for a record."""
    return ", ".join(f"{name} 0x{value:08X}" for name, value in state.items() if value) or "all 0"


def clear_state(regs, items: list) -> None:
    """Clears every error register: one write per register (runs of registers that any write clears with one
    command), then the writes that clear the memory error counters."""
    plain = sorted((r for r in items if isinstance(r, Register) and r.clear is not None and r.select is None),
                   key=lambda r: r.address)
    runs: list[list[Register]] = []
    for r in plain:
        if (runs and r.clear == 0 and runs[-1][-1].clear == 0 and r.address == runs[-1][-1].address + 4
                and len(runs[-1]) < 16):
            runs[-1].append(r)
        else:
            runs.append([r])
    for run in runs:
        regs.write_block(run[0].address, [r.clear for r in run])
    for c in items:
        if isinstance(c, Clear):
            regs.write(c.address, c.value)
