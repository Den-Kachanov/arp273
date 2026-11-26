# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
A extension of the pipelined core (ADR 019): LR, SC and the AMOs, and mhartid

The core sends an atomic as one data request with its operation; the memory model of
test_core.Memory.atomic executes it, as the L2 will. Every program runs on a BRAM and on slow
memories (test_pipeline.both): the commit traces must be identical
"""

import os

import asm
import cocotb
import common
import pytest
from test_core import BASE, CONFIGS, MODULES, config_run, registers, sext32
from test_csr import handler_reads, program
from test_pipeline import both

DATA = 0x1000         # data offset from BASE (auipc x1, 1 at BASE)


@cocotb.test()
async def test_word_atomics(dut):
    """Every .W operation: rd gets the old value sign-extended, memory the new one; the result feeds the next instruction"""
    prog = [
        asm.auipc(1, 1),                    # 0: x1 = BASE + 0x1000
        asm.addi(4, 0, 5),
        asm.sw(4, 0, 1),                    # mem[0] = 5
        asm.addi(2, 0, 7),
        asm.amo("add", "w", 5, 2, 1),       # x5 = 5, mem[0] = 12
        asm.addi(3, 0, -9),
        asm.addi(6, 1, 4),
        asm.sw(2, 0, 6),                    # mem[4] = 7
        asm.amo("min", "w", 7, 3, 6),       # x7 = 7, mem[4] = -9
        asm.amo("maxu", "w", 8, 2, 6),      # x8 = sext(-9), mem[4] = max(0xFFFFFFF7, 7) unsigned
        asm.amo("max", "w", 17, 2, 6),      # x17 = sext(-9), mem[4] = 7
        asm.amo("minu", "w", 18, 3, 6),     # x18 = 7, mem[4] = min(7, 0xFFFFFFF7) = 7
        asm.amo("swap", "w", 12, 4, 1),     # x12 = 12, mem[0] = 5
        asm.lw(13, 0, 1),                   # x13 = 5
        asm.amo("xor", "w", 14, 2, 1),      # x14 = 5, mem[0] = 5 ^ 7 = 2
        asm.add(15, 14, 14),                # uses the AMO result at once
        asm.amo("or", "w", 16, 2, 1),       # x16 = 2, mem[0] = 7
        asm.amo("and", "w", 19, 4, 1),      # x19 = 7, mem[0] = 5
        asm.lw(20, 0, 1),
        asm.lw(21, 4, 1),
        asm.ECALL,
    ]
    (trace, mem, trap_pc), _ = await both(dut, prog)
    r = registers(trace)
    assert r[5] == 5 and r[7] == 7
    assert r[8] == sext32(-9 & 0xFFFFFFFF) and r[17] == r[8], "the old value is sign-extended"
    assert r[18] == 7 and r[12] == 12 and r[13] == 5 and r[14] == 5 and r[15] == 10
    assert r[16] == 2 and r[19] == 7 and r[20] == 5 and r[21] == 7
    assert mem.read(BASE + DATA, 4) == 5 and mem.read(BASE + DATA + 4, 4) == 7
    assert trap_pc == BASE + 4 * (len(prog) - 1)


@cocotb.test()
async def test_word_atomic_in_the_upper_lane(dut):
    """A .W atomic on the upper half of a 64-bit word touches only that half (RV64)"""
    if common.xlen() != 64:
        return
    prog = [
        asm.auipc(1, 1),
        asm.addi(2, 0, -1),
        asm.sd(2, 0, 1),                    # the whole word = all ones
        asm.addi(6, 1, 4),
        asm.addi(3, 0, 1),
        asm.amo("add", "w", 5, 3, 6),       # upper half: 0xFFFFFFFF + 1 = 0; x5 = sext(0xFFFFFFFF) = -1
        asm.ld(7, 0, 1),
        asm.ECALL,
    ]
    (trace, _, _), _ = await both(dut, prog)
    r = registers(trace)
    assert r[5] == common.mask()
    assert r[7] == 0x00000000FFFFFFFF, "the lower half must stay"


@cocotb.test()
async def test_double_atomics(dut):
    """.D operations on 64-bit values; .D is illegal at XLEN = 32 (checked in test_decoder)"""
    if common.xlen() != 64:
        return
    prog = [
        asm.auipc(1, 1),
        asm.addi(2, 0, -1),
        asm.slli(2, 2, 63),                 # x2 = 0x8000000000000000 (most negative)
        asm.addi(3, 0, 1),
        asm.sd(3, 0, 1),                    # mem = 1
        asm.amo("min", "d", 5, 2, 1),       # x5 = 1, mem = 0x8000...
        asm.amo("maxu", "d", 6, 3, 1),      # x6 = 0x8000..., mem = 0x8000... (unsigned larger)
        asm.amo("max", "d", 7, 3, 1),       # x7 = 0x8000..., mem = 1
        asm.lr("d", 8, 1),                  # x8 = 1
        asm.sc("d", 9, 2, 1),               # x9 = 0, mem = 0x8000...
        asm.ld(10, 0, 1),
        asm.ECALL,
    ]
    (trace, mem, _), _ = await both(dut, prog)
    r = registers(trace)
    assert r[5] == 1 and r[6] == 1 << 63 and r[7] == 1 << 63
    assert r[8] == 1 and r[9] == 0 and r[10] == 1 << 63


@cocotb.test()
async def test_lr_sc(dut):
    """SC succeeds only after an LR at the same address, and only once; a failed SC stores nothing"""
    prog = [
        asm.auipc(1, 1),
        asm.addi(2, 0, 11),
        asm.addi(3, 0, 22),
        asm.sc("w", 4, 2, 1),               # no reservation: fails, x4 = 1
        asm.lr("w", 5, 1),                  # x5 = 0
        asm.sc("w", 6, 2, 1),               # succeeds: x6 = 0, mem[0] = 11
        asm.sc("w", 7, 3, 1),               # the reservation is gone: x7 = 1
        asm.addi(9, 1, 4),
        asm.lr("w", 8, 1),                  # reserve mem[0]
        asm.sc("w", 10, 3, 9),              # another address: fails, x10 = 1
        asm.sc("w", 11, 3, 1),              # the failed SC cleared the reservation: x11 = 1
        asm.lw(12, 0, 1),
        asm.lw(13, 4, 1),
        asm.bne(4, 0, 8),                   # the SC result feeds a branch at once
        asm.addi(14, 0, 1),                 # skipped
        asm.ECALL,
    ]
    (trace, _, _), _ = await both(dut, prog)
    r = registers(trace)
    assert (r[4], r[5], r[6], r[7], r[8], r[10], r[11]) == (1, 0, 0, 1, 11, 1, 1)
    assert r[12] == 11 and r[13] == 0
    assert 14 not in r


@cocotb.test()
async def test_atomic_exceptions(dut):
    """A misaligned LR is a load misaligned exception (4), a misaligned SC or AMO a store/AMO one (6); nothing changes"""
    body = [
        asm.lui(5, 1),                      # x5 = 0x1000 (no memory: the accesses trap before they go out)
        asm.addi(5, 5, 2),                  # misaligned for .W
        asm.addi(6, 0, 9),
        asm.lr("w", 7, 5),                  # 4
        asm.sc("w", 8, 6, 5),               # 6
        asm.amo("add", "w", 9, 6, 5),       # 6
        asm.EBREAK,
    ]
    prog, first = program(body)
    pc = lambda k: BASE + 4 * (first + k)
    (trace, mem, _), _ = await both(dut, prog, skip_traps=3)
    assert handler_reads(trace) == [(4, pc(3), 0x1002), (6, pc(4), 0x1002), (6, pc(5), 0x1002)]
    r = registers(trace)
    assert 7 not in r and 8 not in r and 9 not in r
    assert mem.read(0x1000, 4) == 0


@cocotb.test()
async def test_mhartid_and_misa(dut):
    """mhartid reads the HART_ID parameter; misa has the A bit"""
    x = common.xlen()
    prog = [asm.csrr(1, "mhartid"), asm.csrr(2, "misa"), asm.ECALL]
    (trace, _, _), _ = await both(dut, prog)
    r = registers(trace)
    assert r.get(1, 0) == int(os.environ.get("HART_ID", "0"))
    assert r[2] == ((2 if x == 64 else 1) << (x - 2)) | (1 << 0) | (1 << 8) | (1 << 12)


@pytest.mark.parametrize("xlen", common.XLENS)
@pytest.mark.parametrize("config", CONFIGS)
def test_atomic_runner(xlen, config):
    """Build the pipelined core in every configuration for this XLEN and run the A extension tests"""
    config_run("core_pipe", __file__, xlen, config, [str(common.source(m)) for m in MODULES])


@pytest.mark.parametrize("xlen", common.XLENS)
def test_atomic_hart_id_runner(xlen):
    """The same tests on a core built with HART_ID = 5"""
    params = dict(CONFIGS["sol2"], HART_ID=5)
    common.run("core_pipe", __file__, xlen, extra_sources=[str(common.source(m)) for m in MODULES],
               build_args=[f"-G{k}={v}" for k, v in params.items()],
               extra_env={k: str(v) for k, v in params.items()})
