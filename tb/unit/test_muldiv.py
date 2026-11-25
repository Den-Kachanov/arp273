# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
import random

import cocotb
import common
import mext
import pytest
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge


class Unit:
    """
    Drives the multiplier/divider the way the core does: the operands and valid_i stay stable
    until done_o. Operations follow each other without releasing valid_i (back to back),
    as consecutive M instructions do

    Latency from valid_i to done_o is iterations + 1 clock edges from idle (iterations is XLEN / 2 on
    the short path, XLEN otherwise, mext.iterations); right after a finished operation one more edge
    is spent in the DONE state
    """

    def __init__(self, dut):
        self.dut = dut
        self.idle = True

    async def reset(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
        dut.rst_ni.value = 0
        dut.valid_i.value = 0
        dut.funct3_i.value = 0
        dut.word_i.value = 0
        dut.a_i.value = 0
        dut.b_i.value = 0
        await RisingEdge(dut.clk_i)
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.rst_ni.value = 1
        self.idle = True

    def drive(self, funct3, a, b, word=0):
        dut = self.dut
        dut.funct3_i.value = funct3
        dut.word_i.value = word
        dut.a_i.value = a
        dut.b_i.value = b
        dut.valid_i.value = 1

    async def op(self, funct3, a, b, word=0):
        """Run one operation, check the latency, return result_o"""
        dut = self.dut
        edges = mext.iterations(funct3, a, b, word) + (1 if self.idle else 2)
        name = f"{mext.NAMES[funct3]}{'W' if word else ''} {a:#x} {b:#x}"
        self.drive(funct3, a, b, word)
        await ClockCycles(dut.clk_i, edges - 1)
        await FallingEdge(dut.clk_i)
        assert int(dut.done_o.value) == 0, f"done_o one edge too early ({name})"
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        assert int(dut.done_o.value) == 1, f"done_o missing after {edges} edges ({name})"
        self.idle = False
        return int(dut.result_o.value)

    async def release(self):
        """Drop valid_i and let the unit return to idle"""
        self.dut.valid_i.value = 0
        await RisingEdge(self.dut.clk_i)
        await FallingEdge(self.dut.clk_i)
        self.idle = True


def operand_values():
    """Edge operands: 0, 1, 2, 3, -1, -2, max, min, min + 1, bit patterns; around 32 bits for XLEN = 64"""
    x = common.xlen()
    m = common.mask()
    vals = {0, 1, 2, 3, 7, m, m - 1, (1 << (x - 1)) - 1, 1 << (x - 1), (1 << (x - 1)) + 1,
            0x55555555 & m, 0xAAAAAAAA & m}
    if x == 64:
        vals |= {0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xFFFFFFFF80000000, 0x100000000,
                 0x123456789ABCDEF0, 0xFFFFFFFF7FFFFFFF}
    return sorted(vals)


async def check(unit, funct3, a, b, word=0):
    """Run an operation and compare with the model"""
    got = await unit.op(funct3, a, b, word)
    want = mext.expected(funct3, a, b, word)
    name = mext.NAMES[funct3] + ("W" if word else "")
    assert got == want, f"{name}({a:#x}, {b:#x}): expected {want:#x}, got {got:#x}"


@cocotb.test()
async def test_known_cases(dut):
    """Results from the specification, written out by hand (not from the model)"""
    unit = Unit(dut)
    await unit.reset()
    x = common.xlen()
    m = common.mask()
    minv = 1 << (x - 1)

    cases = [
        # (funct3, a, b, expected)
        (0, 6, 7, 42),
        (0, m, m, 1),                           # -1 * -1
        (1, m, m, 0),                           # MULH(-1, -1): product 1, high part 0
        (1, minv, minv, 1 << (x - 2)),          # MIN * MIN = 2^(2x-2)
        (2, m, m, m),                           # MULHSU(-1, 2^x - 1) = -2^x + 1: high part -1
        (2, m, 1, m),                           # MULHSU(-1, 1) = -1: high part -1
        (3, m, m, m - 1),                       # (2^x - 1)^2 = 2^2x - 2^(x+1) + 1
        (4, 7, 0, m),                           # division by zero: all ones
        (5, 7, 0, m),
        (6, 7, 0, 7),                           # remainder by zero: the dividend
        (7, 7, 0, 7),
        (4, minv, m, minv),                     # overflow: MIN / -1 = MIN
        (6, minv, m, 0),                        # MIN % -1 = 0
        (4, (-7) & m, 2, (-3) & m),             # rounds toward zero
        (6, (-7) & m, 2, (-1) & m),             # the remainder takes the sign of the dividend
        (4, 7, (-2) & m, (-3) & m),
        (6, 7, (-2) & m, 1),
        (5, m, 2, m >> 1),                      # unsigned
        (7, m, 10, m % 10),
    ]
    for funct3, a, b, want in cases:
        got = await unit.op(funct3, a, b)
        assert got == want, f"{mext.NAMES[funct3]}({a:#x}, {b:#x}): expected {want:#x}, got {got:#x}"

    if x == 64:
        word_cases = [
            (0, 0x7FFFFFFF, 2, 0xFFFFFFFFFFFFFFFE),         # MULW: 0xFFFFFFFE, sign-extended
            (4, 0x80000000, 0xFFFFFFFF, 0xFFFFFFFF80000000),  # DIVW: INT_MIN / -1 = INT_MIN
            (6, 0x80000000, 0xFFFFFFFF, 0),
            (4, 5, 0, m),                                   # DIVW by zero: -1
            (5, 5, 0, m),                                   # DIVUW by zero: 2^32 - 1, sign-extended
            (6, 0x80000001, 0, 0xFFFFFFFF80000001),         # REMW by zero: the dividend, sign-extended
            (7, 0x80000001, 0, 0xFFFFFFFF80000001),
            (5, 0xFFFFFFFF, 2, 0x7FFFFFFF),                 # DIVUW works on the low 32 bits
            (4, 0xFFFFFFFF00000000 | 7, 2, 3),              # the upper half is ignored
        ]
        for funct3, a, b, want in word_cases:
            got = await unit.op(funct3, a, b, word=1)
            assert got == want, f"{mext.NAMES[funct3]}W({a:#x}, {b:#x}): expected {want:#x}, got {got:#x}"


@cocotb.test()
async def test_edge_values(dut):
    """All eight operations on all pairs of edge operands"""
    unit = Unit(dut)
    await unit.reset()
    vals = operand_values()
    for funct3 in range(8):
        for a in vals:
            for b in vals:
                await check(unit, funct3, a, b)


@cocotb.test()
async def test_random_values(dut):
    """All eight operations on random operands (also with small divisors and small dividends)"""
    unit = Unit(dut)
    await unit.reset()
    r = random.Random(1)
    x = common.xlen()
    for _ in range(60):
        a = r.getrandbits(x)
        b = r.getrandbits(r.choice((x, x // 2, 8, 3)))
        if r.random() < 0.3:
            a = r.getrandbits(r.choice((x // 2, 8)))
        for funct3 in range(8):
            await check(unit, funct3, a, b)


@cocotb.test()
async def test_word_edge_values(dut):
    """W operations on all pairs of edge operands (RV64 only)"""
    if common.xlen() != 64:
        return
    unit = Unit(dut)
    await unit.reset()
    vals = operand_values()
    for funct3 in mext.WORD_FUNCT3:
        for a in vals:
            for b in vals:
                await check(unit, funct3, a, b, word=1)


@cocotb.test()
async def test_word_random_values(dut):
    """W operations on random operands (RV64 only)"""
    if common.xlen() != 64:
        return
    unit = Unit(dut)
    await unit.reset()
    r = random.Random(2)
    for _ in range(60):
        a = r.getrandbits(64)
        b = r.getrandbits(r.choice((64, 32, 8)))
        for funct3 in mext.WORD_FUNCT3:
            await check(unit, funct3, a, b, word=1)


@cocotb.test()
async def test_protocol(dut):
    """done_o: low until the last step, one cycle high, low again; no result without valid_i"""
    unit = Unit(dut)
    await unit.reset()
    x = common.xlen()
    m = common.mask()

    # nothing happens without valid_i
    for _ in range(3 * x):
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        assert int(dut.done_o.value) == 0, "done_o without valid_i"

    # one operation from idle, cycle by cycle: a short one (XLEN / 2 iterations) and a full one (XLEN)
    for funct3, a, b, want, iters in ((4, 100, 7, 14, x // 2), (5, m, 7, None, x)):
        if want is None:
            want = mext.expected(funct3, a, b)
        assert mext.iterations(funct3, a, b) == iters
        unit.drive(funct3, a, b)
        for edge in range(1, iters + 1):
            await RisingEdge(dut.clk_i)
            await FallingEdge(dut.clk_i)
            assert int(dut.done_o.value) == 0, f"done_o after {edge} edges, expected after {iters + 1}"
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        assert int(dut.done_o.value) == 1 and int(dut.result_o.value) == want
        dut.valid_i.value = 0
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        assert int(dut.done_o.value) == 0, "done_o must last one cycle"
        for _ in range(3 * x):
            await RisingEdge(dut.clk_i)
            await FallingEdge(dut.clk_i)
            assert int(dut.done_o.value) == 0, "done_o without valid_i after an operation"

    # the next valid_i right after done_o: the DONE cycle does not swallow or repeat an operation
    unit.idle = True
    assert await unit.op(0, 3, 5) == 15
    assert await unit.op(0, m, m) == 1
    assert await unit.op(4, 9, 3) == 3
    assert unit.idle is False


@cocotb.test()
async def test_operands_are_latched(dut):
    """The unit keeps its own copy of the operands: changing the inputs after the first edge changes nothing"""
    unit = Unit(dut)
    await unit.reset()
    r = random.Random(3)
    x = common.xlen()
    for funct3 in range(8):
        a = r.getrandbits(x)
        b = r.getrandbits(x) | 1
        unit.drive(funct3, a, b)
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        for _ in range(mext.iterations(funct3, a, b)):
            dut.a_i.value = r.getrandbits(x)
            dut.b_i.value = r.getrandbits(x)
            dut.funct3_i.value = r.getrandbits(3)
            await RisingEdge(dut.clk_i)
            await FallingEdge(dut.clk_i)
        assert int(dut.done_o.value) == 1
        assert int(dut.result_o.value) == mext.expected(funct3, a, b), f"{mext.NAMES[funct3]} used changing inputs"
        await unit.release()


@cocotb.test()
async def test_garbage_after_power_up(dut):
    """
    Registers without reset may hold anything after power-up (a chip does not clear them).
    Fill all of them with garbage before the first operation: the result and the latency must not change
    """
    unit = Unit(dut)
    await unit.reset()
    x = common.xlen()
    r = random.Random(5)
    regs = ("hi_q", "lo_q", "opa_q", "cnt_q", "is_div_q", "a_signed_q", "b_signed_q", "pick_hi_q",
            "neg_q", "word_q", "short_q", "last_q")
    for funct3, a, b in ((0, 6, 7), (4, 100, 7), (3, (1 << x) - 1, 3), (6, (-7) & common.mask(), 2)):
        for name in regs:
            sig = getattr(dut, name)
            sig.value = (1 << len(sig)) - 1 if name == "last_q" else r.getrandbits(len(sig))
        unit.idle = True
        await check(unit, funct3, a, b)
        await unit.release()


@cocotb.test()
async def test_short_path_boundaries(dut):
    """Operands at the edge of the short path (ADR 011): results and latency on both sides of each edge"""
    unit = Unit(dut)
    await unit.reset()
    x = common.xlen()
    m = common.mask()
    h = x // 2
    half = 1 << h
    big = (0x9E3779B97F4A7C15 & m) | 1
    cases = [
        # MUL: the multiplier b decides; a may be anything
        (0, big, half - 1), (0, big, half), (0, m, half - 1), (0, m, m), (0, 0, half - 1), (0, half - 1, big),
        # MULH*: never short, even with small operands
        (1, 3, 5), (2, 3, 5), (3, 3, 5),
        # DIV, REM: dividend magnitude below 2^h, signed and unsigned views of the same bits
        (4, half - 1, 3), (4, half, 3), (4, (-(half - 1)) & m, 3), (4, (-half) & m, 3),
        (5, half - 1, 3), (5, half, 3), (5, (-(half - 1)) & m, 3),
        (6, (-(half - 1)) & m, 7), (6, (-half) & m, 7), (7, half - 1, 7), (7, (-1) & m, 7),
        # divisor larger than the dividend, divisor with a full-width value
        (4, 5, big), (5, 5, m), (6, (-5) & m, big), (7, half - 1, m),
        # division by zero is never short
        (4, 5, 0), (5, 5, 0), (6, 5, 0), (7, 5, 0),
        # small values of every kind
        (4, (-7) & m, 2), (4, 7, (-2) & m), (6, (-7) & m, (-2) & m), (4, 0, 1), (5, 1, 1),
        # MIN / -1 and MIN as a dividend take the full path
        (4, 1 << (x - 1), m), (6, 1 << (x - 1), m),
    ]
    short = full = 0
    for funct3, a, b in cases:
        if mext.iterations(funct3, a, b) == h:
            short += 1
        else:
            full += 1
        await check(unit, funct3, a, b)
    assert short >= 12 and full >= 12, "the list must cover both sides"

    if x == 64:
        word_cases = [
            # MULW: the upper halves of a and b are ignored, so MULW is always short
            (0, big, big), (0, 0x7FFFFFFF, 2), (0, m, m),
            # DIVW/DIVUW/REMW/REMUW: a 32-bit dividend always fits; only a zero divisor takes the full path
            (4, 0x80000000, 0xFFFFFFFF), (4, 0x80000000, 1), (5, 0xFFFFFFFF, 3), (6, 0x80000001, 7),
            (7, 0xFFFFFFFF, 0x10), (4, 5, 0), (5, 5, 0), (6, 0x80000001, 0), (7, 0x80000001, 0),
            (4, 0xFFFFFFFF00000000 | 7, 0x100000002), (5, 7, 0x100000000),
        ]
        for funct3, a, b in word_cases:
            await check(unit, funct3, a, b, word=1)


@cocotb.test()
async def test_random_magnitudes(dut):
    """Random operands of every size, so both paths are taken many times; results and latency checked"""
    unit = Unit(dut)
    await unit.reset()
    r = random.Random(4)
    x = common.xlen()
    m = common.mask()
    sizes = (x, x - 1, x // 2 + 1, x // 2, x // 2 - 1, 8, 1)
    for _ in range(150):
        a = r.getrandbits(r.choice(sizes))
        b = r.getrandbits(r.choice(sizes))
        if r.random() < 0.4:
            a = (-a) & m
        if r.random() < 0.2:
            b = (-b) & m
        for funct3 in range(8):
            await check(unit, funct3, a, b)
        if x == 64:
            for funct3 in mext.WORD_FUNCT3:
                await check(unit, funct3, a, b, word=1)


@pytest.mark.parametrize("xlen", common.XLENS)
def test_muldiv_runner(xlen):
    """Build the multiplier/divider for this XLEN and run the cocotb tests"""
    common.run("muldiv", __file__, xlen)
