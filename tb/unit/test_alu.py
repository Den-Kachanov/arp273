# SPDX-License-Identifier: CERN-OHL-S-2.0
import random

import cocotb
import common
import pytest
from cocotb.triggers import Timer

ALU = {
    "ALU_ADD"  : 0b0000,
    "ALU_SLL"  : 0b0001,
    "ALU_SLT"  : 0b0010,
    "ALU_SLTU" : 0b0011,
    "ALU_XOR"  : 0b0100,
    "ALU_SRL"  : 0b0101,
    "ALU_OR"   : 0b0110,
    "ALU_AND"  : 0b0111,
    "ALU_SUB"  : 0b1000,
    "ALU_SRA"  : 0b1101
}

def mask(func):
    """truncate a model result to XLEN bits"""
    return lambda *args, **kwargs: func(*args, **kwargs) & common.mask()

@mask
def add(a, b):
    return a + b

@mask
def sub(a, b):
    return a - b

@mask
def xor(a, b):
    return a ^ b

@mask
def sll(a, b):
    """
    Shift left logical

    Dependencies: common.xlen
    """
    return a << (b & (common.xlen() - 1))

@mask
def srl(a, b):
    """
    Shift right logical

    Dependencies: common.xlen
    """
    return a >> (b & (common.xlen() - 1))

@mask
def sra(a: int, b):
    """
    Shift right arithmetic

    Dependencies: common.xlen
    """
    return common.to_signed(a) >> (b & (common.xlen() - 1))

def slt(a, b):
    """
    a < b, signed

    Dependencies: common.to_signed"""
    return int(common.to_signed(a) < common.to_signed(b))

def sltu(a, b):
    """a < b, unsigned"""
    return int(a < b)

@mask
def _and(a, b):
    return a & b

@mask
def _or(a, b):
    return a | b

ALU_EXPECTED = {
    "ALU_ADD"  : add,
    "ALU_SLL"  : sll,
    "ALU_SLT"  : slt,
    "ALU_SLTU" : sltu,
    "ALU_XOR"  : xor,
    "ALU_SRL"  : srl,
    "ALU_OR"   : _or,
    "ALU_AND"  : _and,
    "ALU_SUB"  : sub,
    "ALU_SRA"  : sra
}

def _sext32(x):
    """
    Sign-extend the low 32 bits of x to XLEN bits

    Dependencies: common.to_signed, common.mask
    """
    return common.to_signed(x, 32) & common.mask()

def addw(a, b):
    """32-bit add, sign-extended"""
    return _sext32(a + b)

def subw(a, b):
    """32-bit subtract, sign-extended"""
    return _sext32(a - b)

def sllw(a, b):
    """32-bit shift left, sign-extended"""
    return _sext32((a & 0xFFFFFFFF) << (b & 31))

def srlw(a, b):
    """32-bit logical shift right, sign-extended"""
    return _sext32((a & 0xFFFFFFFF) >> (b & 31))

def sraw(a, b):
    """32-bit arithmetic shift right, sign-extended"""
    return _sext32(common.to_signed(a, 32) >> (b & 31))

ALU_WORD_EXPECTED = {
    "ALU_ADD"  : addw,
    "ALU_SLL"  : sllw,
    "ALU_SRL"  : srlw,
    "ALU_SUB"  : subw,
    "ALU_SRA"  : sraw
}

async def apply(dut, op, a, b, word=0):
    """Drive the inputs, await, return r_o"""
    dut.op_i.value = op
    dut.a_i.value = a
    dut.b_i.value = b
    dut.word_i.value = word
    await Timer(1, "ns")
    return int(dut.r_o.value)


@cocotb.test()
async def test_edge_values(dut):
    """All ops on edge values (0, 1, -1, max, min)"""
    for alu_op, code in ALU.items():
        for a in common.edge_values():
            for b in common.edge_values():
                result = await apply(dut, code, a, b)
                assert result == (expected := ALU_EXPECTED[alu_op](a, b)), f"{alu_op} failed for ({hex(a)}, {hex(b)})\nExpected: {hex(expected)}, got {hex(result)}"

@cocotb.test()
async def test_random_values(dut):
    """All ops on random values"""
    r = random.Random(1)
    for _ in range(100):
        a = r.getrandbits(common.xlen())
        b = r.getrandbits(common.xlen())
        for alu_op, code in ALU.items():
            result = await apply(dut, code, a, b)
            assert result == (expected := ALU_EXPECTED[alu_op](a, b)), f"{alu_op} failed for ({hex(a)}, {hex(b)})\nExpected: {hex(expected)}, got {hex(result)}"

# WORD TESTS (if xlen == 64)

@cocotb.test()
async def test_word_edge_values(dut):
    """W ops on edge values (RV64 only)"""
    if common.xlen() != 64:
        return

    for alu_op, model in ALU_WORD_EXPECTED.items():
        for a in common.edge_values():
            for b in common.edge_values():
                result = await apply(dut, ALU[alu_op], a, b, word=1)
                assert result == (expected := model(a, b)), f"{alu_op}W failed for ({hex(a)}, {hex(b)})\nExpected: {hex(expected)}, got {hex(result)}"

@cocotb.test()
async def test_word_random_values(dut):
    """W ops on 100 random values (RV64 only)"""
    if common.xlen() != 64:
        return

    r = random.Random(1)
    for _ in range(100):
        a = r.getrandbits(common.xlen())
        b = r.getrandbits(common.xlen())
        for alu_op, model in ALU_WORD_EXPECTED.items():
            result = await apply(dut, ALU[alu_op], a, b, word=1)
            assert result == (expected := model(a, b)), f"{alu_op}W failed for ({hex(a)}, {hex(b)})\nExpected: {hex(expected)}, got {hex(result)}"


@cocotb.test()
async def test_shift_amounts(dut):
    """
    SLL, SRL, SRA by every amount 0..2*XLEN+1, also with upper bits of b set

    Dependencies: common.xlen, common.mask
    """
    x = common.xlen()
    upper = common.mask() & ~(x - 1)
    for alu_op in ("ALU_SLL", "ALU_SRL", "ALU_SRA"):
        for a in (1, common.mask(), 1 << (x - 1), 0x123456789ABCDEF0 & common.mask()):
            for amount in range(2 * x + 2):
                for b in (amount, amount | upper):
                    result = await apply(dut, ALU[alu_op], a, b)
                    assert result == (expected := ALU_EXPECTED[alu_op](a, b)), f"{alu_op} failed for ({hex(a)}, {hex(b)})\nExpected: {hex(expected)}, got {hex(result)}"

@cocotb.test()
async def test_word_shift_amounts(dut):
    """
    SLLW, SRLW, SRAW by every amount 0..69, also with upper bits of b set (RV64 only)

    Dependencies: common.mask
    """
    if common.xlen() != 64:
        return

    upper = common.mask() & ~31
    for alu_op in ("ALU_SLL", "ALU_SRL", "ALU_SRA"):
        for a in (1, common.mask(), 0x80000000, 0xFFFFFFFF80000000, 0x123456789ABCDEF0):
            for amount in range(70):
                for b in (amount, amount | upper):
                    result = await apply(dut, ALU[alu_op], a, b, word=1)
                    assert result == (expected := ALU_WORD_EXPECTED[alu_op](a, b)), f"{alu_op}W failed for ({hex(a)}, {hex(b)})\nExpected: {hex(expected)}, got {hex(result)}"

@cocotb.test()
async def test_word_known_cases(dut):
    """Known W results: sign extension, 5-bit shift amount, bit 31 as sign (RV64 only)"""
    if common.xlen() != 64:
        return

    cases = [
        ("ALU_ADD", 0x7FFFFFFF,         1,  0xFFFFFFFF80000000),
        ("ALU_SLL", 1,                  31, 0xFFFFFFFF80000000),
        ("ALU_SLL", 1,                  32, 1),
        ("ALU_SRL", 0xFFFFFFFF80000000, 1,  0x40000000),
        ("ALU_SRA", 0x80000000,         1,  0xFFFFFFFFC0000000),
    ]
    for alu_op, a, b, expected in cases:
        result = await apply(dut, ALU[alu_op], a, b, word=1)
        assert result == expected, f"{alu_op}W failed for ({hex(a)}, {hex(b)})\nExpected: {hex(expected)}, got {hex(result)}"


@pytest.mark.parametrize("xlen", common.XLENS)
def test_alu_runner(xlen):
    """Build the ALU for this XLEN and run the cocotb tests"""
    common.run("alu", __file__, xlen)
