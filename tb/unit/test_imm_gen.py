# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov

import random

import cocotb
import common
import pytest
from cocotb.triggers import Timer

IMM = {
    "IMM_I": 0b000,
    "IMM_S": 0b001,
    "IMM_B": 0b010,
    "IMM_U": 0b011,
    "IMM_J": 0b100,
}


def mask_xlen(value):
    """Truncate a value to XLEN bits"""
    return value & common.mask()


def sign_extend(value, bits):
    """Sign-extend a signed value to XLEN bits"""
    sign = 1 << (bits - 1)

    if value & sign:
        value -= 1 << bits

    return mask_xlen(value)


def imm_i(inst):
    """I-type immediate: inst[31:20]"""
    value = (inst >> 20) & 0xFFF
    return sign_extend(value, 12)


def imm_s(inst):
    """S-type immediate: inst[31:25] | inst[11:7]"""
    value = (
        (((inst >> 25) & 0x7F) << 5)
        | ((inst >> 7) & 0x1F)
    )

    return sign_extend(value, 12)


def imm_b(inst):
    """B-type immediate: inst[31|7|30:25|11:8|0]"""
    value = (
        (((inst >> 31) & 0x1) << 12)
        | (((inst >> 7) & 0x1) << 11)
        | (((inst >> 25) & 0x3F) << 5)
        | (((inst >> 8) & 0xF) << 1)
    )

    return sign_extend(value, 13)


def imm_u(inst):
    """U-type immediate: inst[31:12] followed by 12 zeros"""
    value = inst & 0xFFFFF000
    return sign_extend(value, 32)


def imm_j(inst):
    """J-type immediate: inst[31|19:12|20|30:21|0]"""
    value = (
        (((inst >> 31) & 0x1) << 20)
        | (((inst >> 12) & 0xFF) << 12)
        | (((inst >> 20) & 0x1) << 11)
        | (((inst >> 21) & 0x3FF) << 1)
    )

    return sign_extend(value, 21)


IMM_EXPECTED = {
    "IMM_I": imm_i,
    "IMM_S": imm_s,
    "IMM_B": imm_b,
    "IMM_U": imm_u,
    "IMM_J": imm_j,
}


async def apply(dut, sel, inst):
    """Drive inputs, await combinational logic, return imm_o"""

    dut.inst_i.value = inst
    dut.sel_i.value = sel

    await Timer(1, "ns")

    return int(dut.imm_o.value)


def edge_instructions():
    """Instructions with important immediate bit patterns"""

    return [
        0x00000000,
        0xFFFFFFFF,
        0x80000000,
        0x7FFFFFFF,
        0xFFFFF000,
        0x00001000,
        0x80000000,
        0x00000800,
        0x00000001,
        0x00000FFF,
        0x000FFFFF,
        0xFFFFF800,
        0xAAAAAAAA,
        0x55555555,
        0xAAAAAAAA & 0xFFFFFFFF,
        0x55555555 & 0xFFFFFFFF,
    ]


@cocotb.test()
async def test_edge_values(dut):
    """All immediate formats on edge instruction patterns"""

    for imm_type, code in IMM.items():
        model = IMM_EXPECTED[imm_type]

        for inst in edge_instructions():
            result = await apply(dut, code, inst)
            expected = model(inst)

            assert result == expected, (
                f"{imm_type} failed for instruction {hex(inst)}\n"
                f"Expected: {hex(expected)}, got: {hex(result)}"
            )


@cocotb.test()
async def test_random_values(dut):
    """All immediate formats on random 32-bit instructions"""

    r = random.Random(1)

    for _ in range(1000):
        inst = r.getrandbits(32)

        for imm_type, code in IMM.items():
            result = await apply(dut, code, inst)
            expected = IMM_EXPECTED[imm_type](inst)

            assert result == expected, (
                f"{imm_type} failed for instruction {hex(inst)}\n"
                f"Expected: {hex(expected)}, got: {hex(result)}"
            )


@cocotb.test()
async def test_sign_extension(dut):
    """Check sign extension for every immediate format"""

    cases = [
        # I-type
        ("IMM_I", 0x80000000),
        ("IMM_I", 0x7FF00000),

        # S-type
        ("IMM_S", 0x80000000),
        ("IMM_S", 0x00000F80),

        # B-type
        ("IMM_B", 0x80000000),
        ("IMM_B", 0x00000800),

        # U-type
        ("IMM_U", 0x80000000),
        ("IMM_U", 0x7FFFF000),

        # J-type
        ("IMM_J", 0x80000000),
        ("IMM_J", 0x00100000),
    ]

    for imm_type, inst in cases:
        code = IMM[imm_type]
        expected = IMM_EXPECTED[imm_type](inst)

        result = await apply(dut, code, inst)

        assert result == expected, (
            f"{imm_type} sign extension failed for {hex(inst)}\n"
            f"Expected: {hex(expected)}, got: {hex(result)}"
        )


@cocotb.test()
async def test_immediate_alignment(dut):
    """B and J immediates must always have bit 0 equal to zero"""

    r = random.Random(2)

    for _ in range(500):
        inst = r.getrandbits(32)

        for imm_type in ("IMM_B", "IMM_J"):
            result = await apply(dut, IMM[imm_type], inst)

            assert (result & 1) == 0, (
                f"{imm_type} alignment failed for {hex(inst)}: "
                f"bit 0 is not zero"
            )


@cocotb.test()
async def test_u_type_known_cases(dut):
    """Known LUI-style values, including RV64 sign extension"""

    cases = [
        (0x00000000, 0x00000000),
        (0x00001000, 0x00001000),
        (0x7FFFF000, 0x7FFFF000),
        (0x80000000, 0xFFFFFFFF80000000 if common.xlen() == 64
         else 0x80000000),
        (0xFFFFF000, common.mask() & ~0xFFF),
    ]

    for inst, expected in cases:
        result = await apply(dut, IMM["IMM_U"], inst)

        assert result == expected, (
            f"U-type failed for {hex(inst)}\n"
            f"Expected: {hex(expected)}, got: {hex(result)}"
        )


@cocotb.test()
async def test_invalid_selector(dut):
    """Unknown immediate selectors must return zero"""

    for sel in (0b101, 0b110, 0b111):
        for inst in edge_instructions():
            result = await apply(dut, sel, inst)

            assert result == 0, (
                f"Invalid selector {bin(sel)} returned {hex(result)}"
            )


@cocotb.test()
async def test_opcode_independence(dut):
    """Immediate generation must not depend on opcode bits"""

    r = random.Random(3)

    for imm_type, code in IMM.items():
        for _ in range(100):
            inst = r.getrandbits(32)

            expected = IMM_EXPECTED[imm_type](inst)

            # Change only opcode bits [6:0]
            modified = (
                (inst & ~0x7F)
                | r.getrandbits(7)
            )

            result = await apply(dut, code, modified)

            assert result == expected, (
                f"{imm_type} depends on opcode bits\n"
                f"Original: {hex(inst)}\n"
                f"Modified: {hex(modified)}\n"
                f"Expected: {hex(expected)}, got: {hex(result)}"
            )


@pytest.mark.parametrize("xlen", common.XLENS)
def test_imm_gen_runner(xlen):
    """Build the immediate generator for this XLEN and run cocotb tests"""

    common.run("imm_gen", __file__, xlen)
