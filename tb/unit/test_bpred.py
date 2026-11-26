# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Branch predictor (rtl/core/bpred.sv, ADR 018): BHT of 2-bit counters, a direct-mapped BTB, a return stack.
The lookup is combinational from pc_i; accept_i makes a call push and a return pop; updates come from EX.
Parameters are the defaults: BHT 64, BTB 16, return stack 8, tag 10 bits, target 20 bits
"""

import cocotb
import common
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

BR, JUMP, CALL, RET = 0, 1, 2, 3
BTB_ENTRIES = 16
RAS_DEPTH = 8
BASE = 0x8000_0000


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    dut.pc_i.value = BASE
    dut.accept_i.value = 0
    dut.upd_i.value = 0
    dut.upd_pc_i.value = 0
    dut.upd_kind_i.value = 0
    dut.upd_taken_i.value = 0
    dut.upd_target_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1


async def update(dut, pc, kind, taken, target=0):
    """one update from EX, applied at the next edge"""
    dut.upd_i.value = 1
    dut.upd_pc_i.value = pc
    dut.upd_kind_i.value = kind
    dut.upd_taken_i.value = taken
    dut.upd_target_i.value = target
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.upd_i.value = 0


async def lookup(dut, pc, accept=False):
    """(taken, target) at pc; with accept the fetch is taken, so a call pushes and a return pops"""
    dut.pc_i.value = pc
    await Timer(1, unit="ns")
    taken, target = int(dut.taken_o.value), int(dut.target_o.value)
    if accept:
        dut.accept_i.value = 1
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.accept_i.value = 0
    return taken, target


@cocotb.test()
async def test_reset_misses(dut):
    """
    after reset nothing is predicted: before the reset every BTB entry holds a valid entry whose tag
    matches the address looked up later, as power-up garbage could; the reset must clear the valid bits
    """
    for i in range(BTB_ENTRIES):
        pc = BASE + 4 * i
        dut.btb_valid_q[i].value = 1
        dut.btb_tag_q[i].value = (pc >> (2 + 4)) & 0x3FF
        dut.btb_kind_q[i].value = JUMP
    await reset(dut)
    for i in range(64):
        taken, _ = await lookup(dut, BASE + 4 * i)
        assert taken == 0, f"prediction at {BASE + 4 * i:#x} after reset"


@cocotb.test()
async def test_branch_learns(dut):
    """a taken branch is predicted taken; the 2-bit counter needs two not-taken outcomes after saturating"""
    await reset(dut)
    p, t = BASE + 0x40, BASE + 0x10
    await update(dut, p, BR, 1, t)
    assert await lookup(dut, p) == (1, t), "taken after the first taken outcome (counter set to 2)"
    await update(dut, p, BR, 0)
    assert (await lookup(dut, p))[0] == 0, "counter 1: not taken"
    await update(dut, p, BR, 1, t)
    await update(dut, p, BR, 1, t)
    await update(dut, p, BR, 1, t)
    await update(dut, p, BR, 0)
    assert (await lookup(dut, p))[0] == 1, "saturated at 3, one not-taken: still taken"
    await update(dut, p, BR, 0)
    assert (await lookup(dut, p))[0] == 0, "two not-taken outcomes: not taken"
    await update(dut, p, BR, 0)
    await update(dut, p, BR, 0)
    await update(dut, p, BR, 0)
    assert (await lookup(dut, p))[0] == 0, "saturated at 0: more not-taken outcomes keep it at 0"
    await update(dut, p, BR, 1, t)
    assert (await lookup(dut, p))[0] == 0, "from 0, one taken outcome gives 1: still not taken"


@cocotb.test()
async def test_not_taken_not_written(dut):
    """a branch that was never taken gets no BTB entry, so it is not predicted"""
    await reset(dut)
    p = BASE + 0x48
    await update(dut, p, BR, 0, BASE + 0x10)
    assert (await lookup(dut, p))[0] == 0


@cocotb.test()
async def test_alias(dut):
    """two pcs with the same BTB index: the later one replaces the earlier"""
    await reset(dut)
    p, q = BASE + 0x80, BASE + 0x80 + 4 * BTB_ENTRIES
    await update(dut, p, JUMP, 1, BASE + 0x200)
    await update(dut, q, JUMP, 1, BASE + 0x300)
    assert await lookup(dut, q) == (1, BASE + 0x300)
    assert (await lookup(dut, p))[0] == 0, "the replaced entry must miss"


@cocotb.test()
async def test_far_target_not_written(dut):
    """a target whose bits above TARGET_BITS differ from the pc's cannot be stored: no entry"""
    await reset(dut)
    p = BASE + 0x100
    await update(dut, p, JUMP, 1, BASE + (1 << 20) + 0x100)
    assert (await lookup(dut, p))[0] == 0


@cocotb.test()
async def test_jump_always_taken(dut):
    await reset(dut)
    p, t = BASE + 0x20, BASE + 0x400
    await update(dut, p, JUMP, 1, t)
    for _ in range(3):
        assert await lookup(dut, p) == (1, t)


@cocotb.test()
async def test_call_return(dut):
    """a call pushes pc + 4 when its fetch is accepted; the return predicts that address and pops it"""
    await reset(dut)
    call, func, ret = BASE + 0x100, BASE + 0x800, BASE + 0x810
    await update(dut, call, CALL, 1, func)
    await update(dut, ret, RET, 1, call + 4)
    assert await lookup(dut, call, accept=True) == (1, func)
    assert await lookup(dut, ret, accept=True) == (1, call + 4)


@cocotb.test()
async def test_ras_overflow(dut):
    """RAS_DEPTH + 1 nested calls: the oldest return address is overwritten (circular stack)"""
    await reset(dut)
    # consecutive words: every call has its own BTB entry (index = pc[5:2]); the return uses index 12
    calls = [BASE + 0x1000 + 4 * i for i in range(RAS_DEPTH + 1)]
    ret = BASE + 0x2000 + 4 * 12
    for c in calls:
        await update(dut, c, CALL, 1, BASE + 0x3000)
    await update(dut, ret, RET, 1, 0)
    for c in calls:
        await lookup(dut, c, accept=True)
    got = [(await lookup(dut, ret, accept=True))[1] for _ in calls]
    want = [c + 4 for c in reversed(calls[1:])] + [calls[-1] + 4]
    assert got == want, f"returns {[hex(g) for g in got]}, expected {[hex(w) for w in want]}"


@cocotb.test()
async def test_lookup_without_accept_keeps_ras(dut):
    """a lookup that is not accepted (fetch stalled) neither pushes nor pops"""
    await reset(dut)
    call, ret = BASE + 0x100, BASE + 0x904     # BTB entries 0 and 1
    await update(dut, call, CALL, 1, BASE + 0x800)
    await update(dut, ret, RET, 1, 0)
    await lookup(dut, call, accept=True)
    for _ in range(3):
        await lookup(dut, ret)
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
    assert await lookup(dut, ret, accept=True) == (1, call + 4)


@cocotb.test()
async def test_push_needs_accept(dut):
    """a call whose fetch is not accepted pushes nothing: the return still predicts the accepted call"""
    await reset(dut)
    a, b, ret = BASE + 0x100, BASE + 0x108, BASE + 0x910   # BTB entries 0, 2 and 4
    await update(dut, a, CALL, 1, BASE + 0x800)
    await update(dut, b, CALL, 1, BASE + 0x800)
    await update(dut, ret, RET, 1, 0)
    await lookup(dut, a, accept=True)
    for _ in range(3):
        await lookup(dut, b)
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
    assert await lookup(dut, ret, accept=True) == (1, a + 4)


@pytest.mark.parametrize("xlen", common.XLENS)
def test_bpred_runner(xlen):
    """Build the branch predictor for this XLEN and run the cocotb tests"""
    common.run("bpred", __file__, xlen)
