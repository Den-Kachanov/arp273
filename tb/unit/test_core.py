# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
import random

import asm
import cocotb
import common
import mext
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

BASE = 0x80000000
MAX_CYCLES = 2000


class Memory:
    """Byte-addressed memory, little endian, XLEN-bit words for data and 32-bit words for instructions"""

    def __init__(self, words):
        self.bytes = {}
        for i, w in enumerate(words):
            for k in range(4):
                self.bytes[BASE + 4 * i + k] = (w >> (8 * k)) & 0xFF

    def read(self, addr, nbytes):
        addr -= addr % nbytes
        return sum(self.bytes.get(addr + k, 0) << (8 * k) for k in range(nbytes))

    def write(self, addr, data, strb, nbytes):
        addr -= addr % nbytes
        for k in range(nbytes):
            if (strb >> k) & 1:
                self.bytes[addr + k] = (data >> (8 * k)) & 0xFF


def is_pipe(dut):
    """The pipelined core has the valid/ready memory ports (ADR 013)"""
    return hasattr(dut, "imem_req_valid_o")


async def run_program(dut, words, max_cycles=MAX_CYCLES):
    """Run a program on either core: see run_single and run_pipe"""
    if is_pipe(dut):
        return await run_pipe(dut, words, max_cycles=max_cycles)
    return await run_single(dut, words, max_cycles=max_cycles)


# probability that a memory port is not ready in a cycle, and the largest extra response latency;
# the pipelined core runs every test against this slow, irregular memory (test_pipeline.py has BRAM)
PIPE_STALL = 0.3
PIPE_EXTRA_LATENCY = 3


async def run_pipe(dut, words, max_cycles=MAX_CYCLES, stall=PIPE_STALL, extra=PIPE_EXTRA_LATENCY, seed=1,
                   istall=None, dstall=None):
    """
    Reset the pipelined core, run the program until trap_o, return (commit trace, memory, trap pc)

    Memory model of ADR 013 for both ports: ready is random (not ready with probability
    `stall`, or istall / dstall per port); an accepted request is answered 1 + random(0..extra)
    cycles later (instruction port: only when istall > 0), in order; a
    store writes the memory when it is accepted. The model checks the protocol: at most one
    request outstanding per port (a new one may be accepted in the cycle the previous response
    is delivered). With stall = 0 and extra = 0 it behaves like a synchronous BRAM

    Dependencies: Memory, common.xlen
    """
    nbytes = common.xlen() // 8
    mem = Memory(words)
    rng = random.Random(seed)
    trace = []
    pend = {"i": None, "d": None}      # (cycle of the response, data)

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    for sig in (dut.imem_req_ready_i, dut.imem_rsp_valid_i, dut.imem_rsp_data_i,
                dut.dmem_req_ready_i, dut.dmem_rsp_valid_i, dut.dmem_rsp_data_i):
        sig.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1

    istall = stall if istall is None else istall
    dstall = stall if dstall is None else dstall

    def latency(port_stall):
        return 1 + (rng.randint(0, extra) if extra and port_stall > 0 else 0)

    for cycle in range(max_cycles):
        # inputs of this cycle: ready and the responses that are due
        dut.imem_req_ready_i.value = int(rng.random() >= istall)
        dut.dmem_req_ready_i.value = int(rng.random() >= dstall)
        due = {}
        for port, valid, data in (("i", dut.imem_rsp_valid_i, dut.imem_rsp_data_i),
                                  ("d", dut.dmem_rsp_valid_i, dut.dmem_rsp_data_i)):
            p = pend[port]
            due[port] = p is not None and p[0] == cycle
            valid.value = int(due[port])
            data.value = p[1] if due[port] else 0
        await Timer(1, "ns")

        if int(dut.trap_o.value):
            assert int(dut.commit_valid_o.value) == 0, "commit during a trap"
            assert not int(dut.dmem_req_valid_o.value), "data request during a trap"
            return trace, mem, int(dut.commit_pc_o.value)

        # requests accepted at the coming edge
        if int(dut.imem_req_valid_o.value):
            assert int(dut.imem_req_addr_o.value) % 4 == 0, "instruction fetch at a misaligned address"
        if int(dut.imem_req_valid_o.value) and int(dut.imem_req_ready_i.value):
            assert pend["i"] is None or due["i"], "instruction port: a second request while one is outstanding"
            pend["i"] = (cycle + latency(istall), mem.read(int(dut.imem_req_addr_o.value), 4))
        elif due["i"]:
            pend["i"] = None
        if int(dut.dmem_req_valid_o.value) and int(dut.dmem_req_ready_i.value):
            assert pend["d"] is None or due["d"], "data port: a second request while one is outstanding"
            addr = int(dut.dmem_req_addr_o.value)
            assert addr % nbytes == 0, f"data request not aligned: {addr:#x}"
            if int(dut.dmem_req_we_o.value):
                mem.write(addr, int(dut.dmem_req_wdata_o.value), int(dut.dmem_req_wstrb_o.value), nbytes)
                pend["d"] = (cycle + latency(dstall), 0)
            else:
                pend["d"] = (cycle + latency(dstall), mem.read(addr, nbytes))
        elif due["d"]:
            pend["d"] = None

        if int(dut.commit_valid_o.value):
            trace.append({"pc": int(dut.commit_pc_o.value), "inst": int(dut.commit_inst_o.value),
                          "we": int(dut.commit_we_o.value), "rd": int(dut.commit_rd_o.value),
                          "data": int(dut.commit_data_o.value)})
        await FallingEdge(dut.clk_i)

    raise AssertionError(f"no trap within {max_cycles} cycles")


async def run_single(dut, words, max_cycles=MAX_CYCLES):
    """
    Reset the single-cycle core, run the program until trap_o, return (commit trace, memory, trap pc)

    The memories are combinational for the core: inputs are answered in the same cycle
    in three short steps, then the clock edge applies the store

    Dependencies: Memory, common.xlen
    """
    nbytes = common.xlen() // 8
    mem = Memory(words)
    trace = []

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    dut.imem_rdata_i.value = 0
    dut.dmem_rdata_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1

    for _ in range(max_cycles):
        await Timer(1, "ns")
        pc = int(dut.imem_addr_o.value)
        dut.imem_rdata_i.value = mem.read(pc, 4)
        await Timer(1, "ns")
        dut.dmem_rdata_i.value = mem.read(int(dut.dmem_addr_o.value), nbytes)
        await Timer(1, "ns")

        if int(dut.trap_o.value):
            assert int(dut.commit_we_o.value) == 0, f"register write during a trap at {pc:#x}"
            assert int(dut.dmem_wstrb_o.value) == 0, f"store during a trap at {pc:#x}"
            assert int(dut.dmem_re_o.value) == 0, f"load during a trap at {pc:#x}"
            return trace, mem, pc
        if not int(dut.commit_valid_o.value):
            # a waiting instruction (multiplier/divider) changes nothing until it is done
            assert int(dut.commit_we_o.value) == 0, f"register write while waiting at {pc:#x}"
            assert int(dut.dmem_wstrb_o.value) == 0, f"store while waiting at {pc:#x}"
        if int(dut.commit_valid_o.value):
            trace.append({"pc": int(dut.commit_pc_o.value), "inst": int(dut.commit_inst_o.value),
                          "we": int(dut.commit_we_o.value), "rd": int(dut.commit_rd_o.value),
                          "data": int(dut.commit_data_o.value)})
        wstrb = int(dut.dmem_wstrb_o.value)
        if wstrb:
            mem.write(int(dut.dmem_addr_o.value), int(dut.dmem_wdata_o.value), wstrb, nbytes)
        await FallingEdge(dut.clk_i)

    raise AssertionError(f"no trap within {max_cycles} cycles")


def registers(trace):
    """Final register values from the commit trace (x0 is never written)"""
    regs = {}
    for c in trace:
        if c["we"]:
            regs[c["rd"]] = c["data"]
    return regs


def sext32(x):
    """Sign-extend the low 32 bits to XLEN bits"""
    return common.to_signed(x, 32) & common.mask()


@cocotb.test()
async def test_sum_1_to_10(dut):
    """Loop with ADD, ADDI and BNE: x1 = 1 + 2 + ... + 10"""
    prog = [
        asm.addi(1, 0, 0),
        asm.addi(2, 0, 1),
        asm.addi(3, 0, 11),
        asm.add(1, 1, 2),       # loop:
        asm.addi(2, 2, 1),
        asm.bne(2, 3, -8),
        asm.ECALL,
    ]
    trace, _, trap_pc = await run_program(dut, prog)
    assert registers(trace)[1] == 55
    assert trap_pc == BASE + 4 * 6, "the trap must be the ECALL"


@cocotb.test()
async def test_alu_instructions(dut):
    """LUI, AUIPC, shifts, comparisons, SUB; a register read and written by one instruction"""
    m = common.mask()
    prog = [
        asm.lui(1, 0x80000),            # 0
        asm.addi(2, 0, -1),             # 1
        asm.addi(3, 0, 1),              # 2
        asm.slli(4, 3, 31),             # 3
        asm.srai(5, 1, 4),              # 4
        asm.sub(6, 0, 3),               # 5
        asm.sltu(7, 3, 2),              # 6
        asm.slt(8, 2, 3),               # 7
        asm.auipc(9, 0),                # 8
        asm.addi(3, 3, 1),              # 9: reads and writes x3
        asm.xor(10, 1, 2),              # 10
        asm.andi(11, 2, 0x7FF),         # 11
        asm.ECALL,
    ]
    trace, _, _ = await run_program(dut, prog)
    r = registers(trace)
    assert r[1] == sext32(0x80000000)
    assert r[2] == m
    assert r[4] == (1 << 31) & m
    assert r[5] == (common.to_signed(r[1]) >> 4) & m
    assert r[6] == m
    assert r[7] == 1 and r[8] == 1
    assert r[9] == BASE + 4 * 8
    assert r[3] == 2, "ADDI x3, x3, 1 must read the old x3"
    assert r[10] == (r[1] ^ m) & m
    assert r[11] == 0x7FF


@cocotb.test()
async def test_word_instructions(dut):
    """ADDIW, ADDW and SUBW sign-extend a 32-bit result (RV64 only)"""
    if common.xlen() != 64:
        return
    prog = [
        asm.addi(1, 0, -1),
        asm.srli(1, 1, 33),             # x1 = 0x7FFFFFFF
        asm.addiw(2, 1, 1),             # 0xFFFFFFFF80000000
        asm.addw(3, 1, 1),              # 0xFFFFFFFFFFFFFFFE
        asm.addi(4, 0, 1),
        asm.subw(5, 0, 4),              # -1
        asm.ECALL,
    ]
    trace, _, _ = await run_program(dut, prog)
    r = registers(trace)
    assert r[1] == 0x7FFFFFFF
    assert r[2] == 0xFFFFFFFF80000000
    assert r[3] == 0xFFFFFFFFFFFFFFFE
    assert r[5] == common.mask()


@cocotb.test()
async def test_load_store(dut):
    """Stores of every size, loads of every size and signedness, partial store keeps the other bytes"""
    xlen64 = common.xlen() == 64
    val = sext32(0x89ABD6EF)
    prog = [
        asm.auipc(1, 1),                # 0: x1 = BASE + 0x1000
        asm.lui(3, 0x89ABD),            # 1
        asm.addi(3, 3, 0x6EF),          # 2: x3 = sext32(0x89ABD6EF)
        asm.sw(3, 0, 1),                # 3
        asm.lw(4, 0, 1),                # 4
        asm.lb(5, 0, 1),                # 5
        asm.lbu(6, 0, 1),               # 6
        asm.lh(7, 0, 1),                # 7
        asm.lhu(8, 0, 1),               # 8
        asm.lb(9, 3, 1),                # 9
        asm.lh(10, 2, 1),               # 10
        asm.addi(14, 0, 0x55),          # 11
        asm.sb(14, 1, 1),               # 12
        asm.lw(15, 0, 1),               # 13
        asm.sh(14, 6, 1),               # 14: halfword at offset 6 (aligned)
        asm.lh(16, 6, 1),               # 15
    ]
    if xlen64:
        prog += [
            asm.lwu(11, 0, 1),
            asm.sd(3, 16, 1),
            asm.ld(12, 16, 1),
            asm.lw(13, 20, 1),
        ]
    prog.append(asm.ECALL)
    trace, _, _ = await run_program(dut, prog)
    r = registers(trace)
    assert r[4] == val
    assert r[5] == common.to_signed(0xEF, 8) & common.mask()
    assert r[6] == 0xEF
    assert r[7] == common.to_signed(0xD6EF, 16) & common.mask()
    assert r[8] == 0xD6EF
    assert r[9] == common.to_signed(0x89, 8) & common.mask()
    assert r[10] == common.to_signed(0x89AB, 16) & common.mask()
    assert r[15] == sext32(0x89AB55EF), "SB must change only one byte"
    assert r[16] == 0x55
    if xlen64:
        assert r[11] == 0x89AB55EF, "LWU after the SB: zero-extended word"
        assert r[12] == val
        assert r[13] == common.mask(), "upper word of x3 is 0xFFFFFFFF, LW sign-extends it"


@cocotb.test()
async def test_jumps_and_branches(dut):
    """JAL and JALR (link, bit 0 cleared), taken and not-taken signed and unsigned branches"""
    prog = [
        asm.addi(1, 0, 0),              # 0
        asm.jal(2, 12),                 # 1: to 4, x2 = pc + 4
        asm.addi(1, 1, 1),              # 2: skipped
        asm.addi(1, 1, 1),              # 3: skipped
        asm.addi(3, 0, 5),              # 4
        asm.auipc(4, 0),                # 5
        asm.addi(4, 4, 13),             # 6: x4 = pc(5) + 13, odd
        asm.jalr(5, 0, 4),              # 7: to pc(5) + 12 = idx 8 after clearing bit 0
        asm.addi(6, 0, 7),              # 8
        asm.addi(7, 0, -1),             # 9
        asm.addi(8, 0, 1),              # 10
        asm.blt(7, 8, 8),               # 11: -1 < 1, taken, to 13
        asm.addi(9, 0, 99),             # 12: skipped
        asm.bltu(7, 8, 8),              # 13: 0xFF.. < 1 unsigned is false
        asm.addi(10, 0, 1),             # 14
        asm.beq(7, 8, 8),               # 15: not taken
        asm.addi(11, 0, 2),             # 16
        asm.bge(7, 8, 8),               # 17: -1 >= 1 is false
        asm.addi(12, 0, 3),             # 18
        asm.ECALL,                      # 19
    ]
    trace, _, trap_pc = await run_program(dut, prog)
    r = registers(trace)
    assert r[1] == 0, "instructions after JAL must be skipped"
    assert r[2] == BASE + 4 * 2
    assert r[5] == BASE + 4 * 8, "JALR link"
    assert 9 not in r, "taken BLT must skip the next instruction"
    assert r[6] == 7 and r[10] == 1 and r[11] == 2 and r[12] == 3
    assert trap_pc == BASE + 4 * 19
    pcs = [c["pc"] for c in trace]
    assert BASE + 4 * 8 in pcs and BASE + 4 * 7 + 6 not in pcs, "JALR target must be aligned"


@cocotb.test()
async def test_perf_counters(dut):
    """Sum loop: instret counts retired instructions, flushes count taken branches, no stalls"""
    prog = [
        asm.addi(1, 0, 0),
        asm.addi(2, 0, 1),
        asm.addi(3, 0, 4),
        asm.add(1, 1, 2),       # loop, 3 iterations
        asm.addi(2, 2, 1),
        asm.bne(2, 3, -8),
        asm.ECALL,
    ]
    trace, _, _ = await run_program(dut, prog)
    assert len(trace) == 3 + 3 * 3
    assert int(dut.perf_instret_o.value) == len(trace)
    assert int(dut.perf_flushes_o.value) == 2, "BNE is taken twice, falls through once"
    if not is_pipe(dut):
        assert int(dut.perf_stalls_o.value) == 0
    assert int(dut.perf_cycles_o.value) >= len(trace)


def check_committed_once(trace, n_instructions):
    """A waiting instruction must be committed exactly once: the pcs are consecutive"""
    pcs = [c["pc"] for c in trace]
    assert pcs == [BASE + 4 * i for i in range(n_instructions)], "an instruction was committed twice or skipped"


@cocotb.test()
async def test_m_instructions(dut):
    """All M instructions: results, one commit each, rd = x0, the same register as source and destination"""
    x = common.xlen()
    m = common.mask()
    a, b = (-7) & m, 2
    prog = [
        asm.addi(1, 0, -7),             # 0
        asm.addi(2, 0, 2),              # 1
        asm.addi(3, 0, 0),              # 2
        asm.mul(4, 1, 2),               # 3
        asm.mulh(5, 1, 2),              # 4
        asm.mulhsu(6, 1, 2),            # 5
        asm.mulhu(7, 1, 2),             # 6
        asm.div(8, 1, 2),               # 7
        asm.divu(9, 1, 2),              # 8
        asm.rem(10, 1, 2),              # 9
        asm.remu(11, 1, 2),             # 10
        asm.div(12, 1, 3),              # 11: by zero
        asm.rem(13, 1, 3),              # 12: by zero
        asm.addi(20, 0, 1),             # 13
        asm.slli(20, 20, x - 1),        # 14: x20 = MIN
        asm.addi(21, 0, -1),            # 15
        asm.div(22, 20, 21),            # 16: MIN / -1
        asm.rem(23, 20, 21),            # 17
        asm.mul(0, 1, 2),               # 18: rd = x0: waits, writes nothing
        asm.mul(1, 1, 1),               # 19: x1 = 49, reads and writes x1
        asm.addi(14, 0, 5),             # 20
    ]
    if x == 64:
        prog += [
            asm.mulw(15, 2, 2),         # 21
            asm.divw(16, 20, 21),       # 22: upper half of x20 is 0, W works on the low 32 bits
            asm.divuw(17, 21, 2),       # 23
            asm.remw(18, 21, 2),        # 24
            asm.remuw(19, 21, 2),       # 25
        ]
    n = len(prog)
    n_md = sum(1 for w in prog if (w & 0x7F) in (asm.OPC["OP"], asm.OPC["OP_32"]) and (w >> 25) == 1)
    # the operands each M instruction sees, in program order: the stall count depends on them (ADR 011)
    minv = 1 << (x - 1)
    md_ops = [(0, a, b, 0), (1, a, b, 0), (2, a, b, 0), (3, a, b, 0), (4, a, b, 0), (5, a, b, 0), (6, a, b, 0),
              (7, a, b, 0), (4, a, 0, 0), (6, a, 0, 0), (4, minv, m, 0), (6, minv, m, 0), (0, a, b, 0), (0, a, a, 0)]
    if x == 64:
        md_ops += [(0, 2, 2, 1), (4, minv, m, 1), (5, m, 2, 1), (6, m, 2, 1), (7, m, 2, 1)]
    assert len(md_ops) == n_md
    prog.append(asm.ECALL)
    trace, _, trap_pc = await run_program(dut, prog, max_cycles=3000)
    r = registers(trace)
    check_committed_once(trace, n)
    assert trap_pc == BASE + 4 * n

    for reg, f3 in ((4, 0), (5, 1), (6, 2), (7, 3), (8, 4), (9, 5), (10, 6), (11, 7)):
        assert r[reg] == mext.expected(f3, a, b), f"{mext.NAMES[f3]}: {r[reg]:#x}"
    assert r[12] == m and r[13] == a, "division by zero"
    assert r[22] == 1 << (x - 1) and r[23] == 0, "MIN / -1"
    assert r[1] == 49, "MUL x1, x1, x1"
    assert all(not (c["we"] and c["rd"] == 0) for c in trace)
    assert r[14] == 5, "the instruction after the last M must run"
    if x == 64:
        # x2 = 2, x20 = MIN (low 32 bits are 0), x21 = -1
        assert r[15] == 4, "MULW"
        assert r[16] == 0, "DIVW: low word of MIN is 0, 0 / -1 = 0"
        assert r[17] == 0x7FFFFFFF, "DIVUW: 0xFFFFFFFF / 2"
        assert r[18] == m, "REMW: -1 % 2 = -1, sign-extended"
        assert r[19] == 1, "REMUW: 0xFFFFFFFF % 2"
        assert r[16] == mext.expected(4, 1 << 63, m, 1) and r[17] == mext.expected(5, m, 2, 1)

    assert int(dut.perf_instret_o.value) == len(trace)
    if not is_pipe(dut):
        want_stalls = sum(mext.iterations(*op) + 1 for op in md_ops)
        assert int(dut.perf_stalls_o.value) == want_stalls, "each M instruction waits its iterations + 1 cycles"
        assert int(dut.perf_cycles_o.value) == len(trace) + int(dut.perf_stalls_o.value)


@cocotb.test()
async def test_m_in_a_loop(dut):
    """Factorial with MUL in a loop: the multiplier result feeds the loop, branches around M instructions work"""
    prog = [
        asm.addi(1, 0, 1),
        asm.addi(2, 0, 5),
        asm.mul(1, 1, 2),       # loop
        asm.addi(2, 2, -1),
        asm.bne(2, 0, -8),
        asm.addi(3, 0, 100),
        asm.addi(4, 0, 7),
        asm.div(5, 3, 4),       # 100 / 7 = 14
        asm.rem(6, 3, 4),       # 100 % 7 = 2
        asm.ECALL,
    ]
    trace, _, _ = await run_program(dut, prog, max_cycles=3000)
    r = registers(trace)
    assert r[1] == 120 and r[5] == 14 and r[6] == 2
    # all seven M instructions here have small operands: the short path, XLEN / 2 iterations each
    md_ops = [(0, 1, 5), (0, 5, 4), (0, 20, 3), (0, 60, 2), (0, 120, 1), (4, 100, 7), (6, 100, 7)]
    x = common.xlen()
    assert all(mext.iterations(*op) == x // 2 for op in md_ops)
    if not is_pipe(dut):
        assert int(dut.perf_stalls_o.value) == sum(mext.iterations(*op) + 1 for op in md_ops)
    assert int(dut.perf_flushes_o.value) == 4, "BNE is taken 4 times"


@cocotb.test()
async def test_x0_is_never_written(dut):
    """Instructions with rd = x0 do not appear as register writes and x0 stays 0"""
    prog = [
        asm.addi(0, 0, 5),
        asm.add(1, 0, 0),
        asm.lui(0, 1),
        asm.add(2, 0, 0),
        asm.ECALL,
    ]
    trace, _, _ = await run_program(dut, prog)
    assert all(c["rd"] != 0 for c in trace if c["we"])
    r = registers(trace)
    assert r[1] == 0 and r[2] == 0


@cocotb.test()
async def test_traps(dut):
    """An illegal instruction and a misaligned load stop the core without side effects"""
    trace, _, trap_pc = await run_program(dut, [asm.addi(1, 0, 3), 0x00000000, asm.addi(2, 0, 4)])
    assert trap_pc == BASE + 4 and registers(trace) == {1: 3}, "illegal instruction"


@cocotb.test()
async def test_misaligned_load_traps(dut):
    """LW at an address that is not a multiple of 4 stops the core and writes nothing"""
    prog = [asm.auipc(1, 1), asm.addi(3, 0, 9), asm.lw(2, 1, 1), asm.addi(4, 0, 4)]
    trace, _, trap_pc = await run_program(dut, prog)
    r = registers(trace)
    assert trap_pc == BASE + 8
    assert 2 not in r and 4 not in r and r[3] == 9


@cocotb.test()
async def test_misaligned_jump_traps(dut):
    """JAL, JALR and a taken branch to a target that is not 4-byte aligned trap on the jump itself, without a link write"""
    # JAL to pc + 6
    trace, _, trap_pc = await run_program(dut, [asm.addi(1, 0, 1), asm.jal(2, 6), asm.addi(3, 0, 3)])
    assert trap_pc == BASE + 4 and registers(trace) == {1: 1}, "JAL must not write the link register"
    # JALR to pc + 10: bit 1 is set, bit 0 is clear
    prog = [asm.auipc(5, 0), asm.addi(5, 5, 10), asm.jalr(6, 0, 5), asm.addi(7, 0, 7)]
    trace, _, trap_pc = await run_program(dut, prog)
    assert trap_pc == BASE + 8 and 6 not in registers(trace) and 7 not in registers(trace)
    # taken BEQ to pc + 6
    trace, _, trap_pc = await run_program(dut, [asm.addi(1, 0, 1), asm.beq(1, 1, 6), asm.addi(2, 0, 2)])
    assert trap_pc == BASE + 4 and registers(trace) == {1: 1}


@cocotb.test()
async def test_misaligned_jump_target_not_taken_is_fine(dut):
    """A branch that is not taken never raises the exception, whatever its offset; JALR bit 0 is cleared, not an error"""
    prog = [
        asm.addi(1, 0, 1),
        asm.bne(1, 1, 6),               # not taken: offset is misaligned but unused
        asm.addi(2, 0, 2),
        asm.auipc(5, 0),                # pc = BASE + 12
        asm.addi(5, 5, 21),             # BASE + 33 = BASE + 32 + 1: bit 0 set, bit 1 clear
        asm.jalr(6, 0, 5),              # target BASE + 32 (index 8) after clearing bit 0
        asm.addi(3, 0, 3),              # skipped
        asm.addi(4, 0, 4),              # skipped
        asm.addi(8, 0, 8),              # index 8
        asm.ECALL,
    ]
    trace, _, trap_pc = await run_program(dut, prog)
    r = registers(trace)
    assert r[2] == 2 and 3 not in r and 4 not in r and r[8] == 8
    assert r[6] == BASE + 4 * 6, "JALR link"
    assert trap_pc == BASE + 4 * 9, "the trap is the ECALL, not a misaligned fetch"


@cocotb.test()
async def test_misaligned_store_traps(dut):
    """SW at an address that is not a multiple of 4 stops the core without a store"""
    prog = [asm.auipc(1, 1), asm.addi(3, 0, 9), asm.sw(3, 2, 1), asm.addi(4, 0, 4)]
    _, mem, trap_pc = await run_program(dut, prog)
    assert trap_pc == BASE + 8
    assert mem.read(BASE + 0x1000, 4) == 0, "nothing may be stored"


MODULES = ("decoder", "regfile", "imm_gen", "alu", "branch_unit", "lsu", "perf_counters", "muldiv")


@pytest.mark.parametrize("xlen", common.XLENS)
def test_core_runner(xlen):
    """Build the single-cycle core with all its modules for this XLEN and run the cocotb tests"""
    common.run("core", __file__, xlen, extra_sources=[str(common.REPO / f"rtl/core/{m}.sv") for m in MODULES])


@pytest.mark.parametrize("xlen", common.XLENS)
def test_core_pipe_runner(xlen):
    """The same programs on the pipelined core, with a slow memory that stalls at random"""
    common.run("core_pipe", __file__, xlen, extra_sources=[str(common.REPO / f"rtl/core/{m}.sv") for m in MODULES])
