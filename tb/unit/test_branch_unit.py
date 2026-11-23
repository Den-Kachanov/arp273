# SPDX-License-Identifier: CERN-OHL-S-2.0
import random

import cocotb
import common
import pytest
from cocotb.triggers import Timer

# funct3 -> comparison on (a, b) as unsigned values of XLEN bits
BRANCH = {
    "BEQ":  (0b000, lambda a, b: a == b),
    "BNE":  (0b001, lambda a, b: a != b),
    "BLT":  (0b100, lambda a, b: common.to_signed(a) < common.to_signed(b)),
    "BGE":  (0b101, lambda a, b: common.to_signed(a) >= common.to_signed(b)),
    "BLTU": (0b110, lambda a, b: a < b),
    "BGEU": (0b111, lambda a, b: a >= b),
}


async def apply(dut, funct3, a, b):
    """Drive the inputs, await, return taken_o"""
    dut.funct3_i.value = funct3
    dut.a_i.value = a
    dut.b_i.value = b
    await Timer(1, "ns")
    return int(dut.taken_o.value)


@cocotb.test()
async def test_edge_values(dut):
    """All conditions on edge values (0, 1, -1, max, min)"""
    for name, (f3, model) in BRANCH.items():
        for a in common.edge_values():
            for b in common.edge_values():
                result = await apply(dut, f3, a, b)
                assert result == int(model(a, b)), f"{name} failed for ({hex(a)}, {hex(b)}): got {result}"


@cocotb.test()
async def test_random_values(dut):
    """All conditions on random values, and on equal values"""
    r = random.Random(1)
    for _ in range(200):
        a = r.getrandbits(common.xlen())
        for b in (r.getrandbits(common.xlen()), a):
            for name, (f3, model) in BRANCH.items():
                result = await apply(dut, f3, a, b)
                assert result == int(model(a, b)), f"{name} failed for ({hex(a)}, {hex(b)}): got {result}"


@cocotb.test()
async def test_signed_vs_unsigned(dut):
    """-1 < 0 as signed but -1 > 0 as unsigned"""
    minus_one = common.mask()
    assert await apply(dut, BRANCH["BLT"][0], minus_one, 0) == 1
    assert await apply(dut, BRANCH["BLTU"][0], minus_one, 0) == 0
    assert await apply(dut, BRANCH["BGE"][0], minus_one, 0) == 0
    assert await apply(dut, BRANCH["BGEU"][0], minus_one, 0) == 1


@cocotb.test()
async def test_reserved_funct3(dut):
    """funct3 = 2 and 3 are not branches: never taken"""
    for f3 in (0b010, 0b011):
        for a, b in ((0, 0), (1, 1), (0, 1), (common.mask(), 0)):
            assert await apply(dut, f3, a, b) == 0, f"funct3 {f3} taken for ({hex(a)}, {hex(b)})"


@pytest.mark.parametrize("xlen", common.XLENS)
def test_branch_unit_runner(xlen):
    """Build the branch unit for this XLEN and run the cocotb tests"""
    common.run("branch_unit", __file__, xlen)
