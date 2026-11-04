# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Steps of TC-RS-HW-19, the pipeline reset at rest, that every reference system takes; the reset step itself, the
reload counters of the keys and the check of the erased key slots are in sw/refsys/systest.py.

The test case of a system leaves values in the processing path (the reset group of the Composer file,
systest.System.pipeline) and outside it, sends the writes that the pipeline reset controller must ignore, resets the
pipeline, checks that every member holds its reset values and that everything outside kept its own, resets a second
time, restores what it changed outside and configures the system again, then checks a round of traffic. The values
of the reset come from the generics of the Composer file and the register maps of the cores.
"""

import random

from refsys import errorstate
from refsys.spptest import write_entry
from refsys.systest import (EDAC_IRQ_ENA, OFB, OMAP, PRST_COUNT_SHIFT, PRST_CTRL, PRST_STATUS, SPP, WIN_CTRL_WRITTEN,
                            WIN_ENABLE, Registers, check, check_link_state, generic_value, generic_words, hex_words,
                            lane_records, record)

# The entries that the test case leaves in the APID tables of the Space Packet node, at APIDs that the run uses for
# nothing else (the inputs are input_apid_base + i, the outputs the APIDs of the send ports)
RX_APID, TX_APID, TX_COUNT = 0x030, 0x031, 0x0123
# The values that the test case leaves outside the processing path: IRQ_ENA of the ECC monitor and DL_BC_INTERVAL of
# the SpaceFibre port (their values of the reset are 0 and 0x28)
IRQ_ENA_VALUE, BC_INTERVAL_VALUE = 0x1, 0x0123


def rx_entry(ch_port: int = 1) -> int:
    """The entry of the receive APID table left at RX_APID: enabled, to receive port ch_port, with the continuity
    check."""
    return (SPP.field("RX_TABLE_ENTRY", "ENABLE", 1) | SPP.field("RX_TABLE_ENTRY", "PORT", ch_port)
            | SPP.field("RX_TABLE_ENTRY", "CHECK", 1))


def tx_entry(regs: Registers, node: int, apid: int) -> int:
    """The entry of the send APID table of an APID (TX_TABLE_APID, then TX_TABLE_ENTRY)."""
    regs.wait(node + SPP.TABLE_STATUS, SPP.field("TABLE_STATUS", "TX_BUSY", 1), 0)
    regs.write(node + SPP.TX_TABLE_APID, apid)
    return regs.read(node + SPP.TX_TABLE_ENTRY)


def rx_entry_of(regs: Registers, node: int, apid: int) -> int:
    """The entry of the receive APID table of an APID (RX_TABLE_APID, then RX_TABLE_ENTRY)."""
    regs.wait(node + SPP.TABLE_STATUS, SPP.field("TABLE_STATUS", "RX_BUSY", 1), 0)
    regs.write(node + SPP.RX_TABLE_APID, apid)
    return regs.read(node + SPP.RX_TABLE_ENTRY)


def leave_in_node(regs: Registers, ctx) -> None:
    """Values left in the Space Packet node: the entry RX_APID of the receive APID table, the count TX_COUNT in the
    entry TX_APID of the send APID table, CONFIG.ROUND_ROBIN; RX_PACKETS and TX_PACKETS hold the counts of the
    traffic of the test case."""
    s = ctx.system
    node = s.base(s.node)
    write_entry(regs, node, RX_APID, rx_entry())
    tx = SPP.field("TX_TABLE_ENTRY", "ENABLE", 1) | SPP.field("TX_TABLE_ENTRY", "COUNT", TX_COUNT)
    tx_entry(regs, node, TX_APID)
    regs.write(node + SPP.TX_TABLE_ENTRY, tx)
    regs.expect(node + SPP.TX_TABLE_ENTRY, tx, f"send APID table entry of APID 0x{TX_APID:03X}")
    config = regs.read(node + SPP.CONFIG)
    regs.write(node + SPP.CONFIG, config | SPP.field("CONFIG", "ROUND_ROBIN", 1))
    for name in ("RX_PACKETS", "TX_PACKETS"):
        count = regs.read(node + getattr(SPP, name))
        check(count > 0, f"{s.node} {name} is 0 before the pipeline reset: the test case left no count")


def outside_items(ctx) -> list:
    """The error registers of the blocks outside the processing path (refsys.errorstate)."""
    return [r for r in ctx.system.error_registers()
            if not isinstance(r, errorstate.Register) or r.name.split(".")[0] not in ctx.system.pipeline]


def member_items(ctx) -> list:
    """The error registers of the members of the reset group."""
    return [r for r in ctx.system.error_registers()
            if isinstance(r, errorstate.Register) and r.name.split(".")[0] in ctx.system.pipeline]


def leave_outside(regs: Registers, ctx) -> dict:
    """Values left outside the processing path: WIN_CTRL (ENABLE 0), WIN_BASE and WIN_LAST of the last window of the
    RMAP target, IRQ_ENA of the ECC monitor and DL_BC_INTERVAL of the SpaceFibre port, each read back; then the
    lanes, TGT_CNT_OK of the RMAP target and the error registers of the blocks outside are read. Returns what
    check_outside and restore_outside need."""
    s = ctx.system
    rng = random.Random(f"{ctx.seed} TC-RS-HW-19")
    window = s.file.generic(s.rmap, "Windows_g") - 1
    win = s.base(s.rmap) + OMAP.WIN_CTRL(window)
    before = regs.read_block(win, 3)
    check(not before[0] & WIN_ENABLE, f"window {window} of {s.rmap} is enabled: the test does not change it")
    words = [rng.getrandbits(32) & WIN_CTRL_WRITTEN, rng.getrandbits(32), rng.getrandbits(32)]
    regs.write_block(win, words)
    single = [regs.read(win + 4 * k) for k in range(3)]
    check(single == words, f"{s.rmap} WIN_CTRL({window}) to WIN_LAST({window}): {hex_words(single)}, expected "
          f"{hex_words(words)}")
    irq = s.base(s.ecc) + EDAC_IRQ_ENA
    bc = s.base(s.port) + OFB.DL_BC_INTERVAL
    saved = {irq: regs.read(irq), bc: regs.read(bc)}
    for address, value in ((irq, IRQ_ENA_VALUE), (bc, BC_INTERVAL_VALUE)):
        regs.write(address, value)
        regs.expect(address, value)
    lanes = lane_records(regs, ctx, "before the pipeline reset: ")
    return {"window": (win, before, words), "saved": saved, "written": {irq: IRQ_ENA_VALUE, bc: BC_INTERVAL_VALUE},
            "lanes": lanes, "ok": regs.read(s.base(s.rmap) + OMAP.TGT_CNT_OK),
            "errors": errorstate.read_state(regs, outside_items(ctx))}


def ignored_writes(regs: Registers, ctx) -> None:
    """The writes that the pipeline reset controller ignores (olo_ft_reset_ctrl_axi, CTRL): another value at CTRL,
    the low half of the key with two bytes (byte enables "0011"), the key at STATUS. Every write is answered with the
    status 0; after each STATUS.COUNT is unchanged and the entry RX_APID of the receive APID table still reads
    back."""
    s = ctx.system
    base = s.base(s.reset)
    node = s.base(s.node)
    count = regs.read(base + PRST_STATUS) >> PRST_COUNT_SHIFT
    key = s.reset_key
    cases = (("another value at CTRL", base + PRST_CTRL, (key ^ 1).to_bytes(4, "little")),
             ("the low half of the key at CTRL", base + PRST_CTRL, (key & 0xFFFF).to_bytes(2, "little")),
             ("the key at STATUS", base + PRST_STATUS, key.to_bytes(4, "little")))
    for what, address, data in cases:
        regs.write_bytes(address, data)
        after = regs.read(base + PRST_STATUS) >> PRST_COUNT_SHIFT
        check(after == count, f"{s.reset}: {what} changes STATUS.COUNT from {count} to {after}")
        entry = rx_entry_of(regs, node, RX_APID)
        check(entry == rx_entry(), f"{s.reset}: after {what} the receive APID table entry of APID 0x{RX_APID:03X} "
              f"reads 0x{entry:08X}, written 0x{rx_entry():08X}")
        ctx.log.emit("reset", f"{s.reset}: {what} ({len(data)} bytes) ignored, STATUS.COUNT {count}", "pass")


def check_node_reset(regs: Registers, ctx) -> None:
    """The Space Packet node after a pipeline reset: the entries RX_APID and TX_APID hold the path defaults (enabled,
    to port or destination 0, count 0), the send APID table entry of every output APID has the count 0, CONFIG,
    MAX_LENGTH and the registers that report the generics their values of the reset (generic_words), RX_PACKETS,
    TX_PACKETS and RX_LOSS 0."""
    s = ctx.system
    node = s.base(s.node)
    rx_default = SPP.field("RX_TABLE_ENTRY", "ENABLE", 1)
    entry = rx_entry_of(regs, node, RX_APID)
    check(entry == rx_default, f"receive APID table entry of APID 0x{RX_APID:03X}: 0x{entry:08X}, expected the path "
          f"default 0x{rx_default:08X}")
    tx_default = SPP.field("TX_TABLE_ENTRY", "ENABLE", 1)
    for apid in [TX_APID] + [ch.output_apid for ch in s.channels]:
        entry = tx_entry(regs, node, apid)
        check(entry == tx_default, f"send APID table entry of APID 0x{apid:03X}: 0x{entry:08X}, expected the path "
              f"default 0x{tx_default:08X}")
    spp = s.file.generics(s.node)
    config = (SPP.field("CONFIG", "ROUND_ROBIN", generic_value(spp["RoundRobin_g"]))
              | SPP.field("CONFIG", "TLA", generic_value(spp["Tla_g"])))
    regs.expect(node + SPP.CONFIG, config, f"{s.node} CONFIG after the pipeline reset")
    regs.expect(node + SPP.MAX_LENGTH, spp["MaxLength_g"], f"{s.node} MAX_LENGTH after the pipeline reset")
    for name, value in generic_words(s.file, s)[s.node].items():
        regs.expect(node + getattr(SPP, name), value, f"{s.node} {name}")
    for name in ("RX_PACKETS", "TX_PACKETS", "RX_LOSS"):
        regs.expect(node + getattr(SPP, name), 0, f"{s.node} {name} after the pipeline reset")


def check_members_errors(regs: Registers, ctx) -> None:
    """Every error and memory error register of the members of the reset group reads 0 after a pipeline reset."""
    items = member_items(ctx)
    state = errorstate.read_state(regs, items)
    problems = errorstate.unexpected(items, state)
    check(not problems, f"error registers of the processing path after the pipeline reset: {', '.join(problems)}")


def check_outside(regs: Registers, ctx, left: dict) -> None:
    """Everything outside the processing path kept its state through the pipeline reset: the window of the RMAP
    target, IRQ_ENA and DL_BC_INTERVAL as written; the link initialised with every lane active, the lanes as
    recorded before; the counters of TGT_CNT_OK of the RMAP target not below their values before (saturating, they
    were not reset); every error register of the blocks outside as before (a leaky counter at least its value
    before)."""
    s = ctx.system
    win, _, words = left["window"]
    got = regs.read_block(win, 3)
    check(got == words, f"{s.rmap} window after the pipeline reset: {hex_words(got)}, written {hex_words(words)}")
    for address, value in left["written"].items():
        regs.expect(address, value)
    check_link_state(regs, ctx)
    lanes = lane_records(regs, ctx, "after the pipeline reset: ")
    check(lanes == left["lanes"], f"lane states {lanes} after the pipeline reset, {left['lanes']} before")
    ok = regs.read(s.base(s.rmap) + OMAP.TGT_CNT_OK)
    counted_on = all(OMAP.get("TGT_CNT_OK", f, ok) >= OMAP.get("TGT_CNT_OK", f, left["ok"]) for f in ("READ", "WRITE"))
    check(counted_on, f"{s.rmap} TGT_CNT_OK 0x{ok:08X} after the pipeline reset, 0x{left['ok']:08X} before: a "
          "counter of the RMAP target went back")
    items = outside_items(ctx)
    state = errorstate.read_state(regs, items)
    for r in items:
        if isinstance(r, errorstate.Register):
            now, then = state[r.name] & r.errors, left["errors"][r.name] & r.errors
            check(now >= then if r.leaky else now == then, f"{r.name} 0x{state[r.name]:08X} after the pipeline "
                  f"reset, 0x{left['errors'][r.name]:08X} before")
    record(ctx, f"outside the processing path after the pipeline reset: {s.rmap} TGT_CNT_OK 0x{ok:08X} "
           f"(0x{left['ok']:08X} before), error registers {errorstate.nonzero(state)}")


def restore_outside(regs: Registers, ctx, left: dict) -> None:
    """The window of the RMAP target, IRQ_ENA and DL_BC_INTERVAL back at their values before the test case."""
    win, before, _ = left["window"]
    regs.write_block(win, before)
    check(regs.read_block(win, 3) == before, f"window of {ctx.system.rmap} is not restored")
    for address, value in left["saved"].items():
        regs.write(address, value)
        regs.expect(address, value)
