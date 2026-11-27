# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
The SoC (rtl/soc/soc_top.sv, ADR 019): two Sol cores, the arbiters, the CLINT and the atomics unit
together. Every program starts on both harts at 0x8000_0000 and branches on mhartid to hart 1's part at
word HART1; hart 0 ends the run with EBREAK. The memory outside (instruction and data port) is modelled
here; it stalls at random in the slow runs. The C programs of sw/smp (make smp) test the same on Spike's
terms; these tests can be run by the mutation script
"""

import random

import asm
import cocotb
import common
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from test_core import BASE, CONFIGS, MODULES, Memory

NH = 2
HART1 = 64              # word index of hart 1's code
DATA = 0x1000           # data, from BASE
CLINT = 0x0200_0000


def program(hart0, hart1):
    """mhartid != 0 jumps to word HART1; hart 1's code ends in an endless loop"""
    words = [asm.csrr(1, "mhartid"), asm.bne(1, 0, 4 * (HART1 - 1)), asm.auipc(30, 1), asm.addi(30, 30, -8)]
    words += hart0 + [asm.EBREAK]
    assert len(words) <= HART1
    words += [asm.NOP] * (HART1 - len(words))
    # hart 1: x30 = BASE + DATA, as for hart 0
    words += [asm.auipc(30, 1), asm.addi(30, 30, -4 * HART1)] + hart1 + [asm.jal(0, 0)]
    return words


def li(rd, value):
    hi = (value + 0x800) >> 12
    return [asm.lui(rd, hi & 0xFFFFF), asm.addi(rd, rd, value - (hi << 12))]


async def run_soc(dut, words, stall=0.0, seed=1, max_cycles=20000):
    """Run until hart 0 traps; return (memory, trace of each hart, trap pc of hart 0)"""
    nbytes = common.xlen() // 8
    mem = Memory(words)
    rng = random.Random(seed)
    traces = [[] for _ in range(NH)]
    pend: dict[str, tuple[int, int] | None] = {"i": None, "d": None}
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    dut.tick_i.value = 1
    dut.irq_meip_i.value = 0
    for s in (dut.mem_i_req_ready_i, dut.mem_i_rsp_valid_i, dut.mem_i_rsp_data_i, dut.mem_d_req_ready_i,
              dut.mem_d_rsp_valid_i, dut.mem_d_rsp_data_i):
        s.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1
    X = common.xlen()

    def lat():
        return 1 + (rng.randrange(3) if stall else 0)

    for cycle in range(max_cycles):
        dut.mem_i_req_ready_i.value = int(rng.random() >= stall)
        dut.mem_d_req_ready_i.value = int(rng.random() >= stall)
        due = {}
        for port, valid, data in (("i", dut.mem_i_rsp_valid_i, dut.mem_i_rsp_data_i),
                                  ("d", dut.mem_d_rsp_valid_i, dut.mem_d_rsp_data_i)):
            p = pend[port]
            due[port] = p is not None and p[0] == cycle
            valid.value = int(due[port])
            data.value = p[1] if (due[port] and p is not None) else 0
        await Timer(1, "ns")
        traps = int(dut.trap_o.value)
        if traps & 1:
            return mem, traces, int(dut.commit_pc_o.value) & ((1 << X) - 1)
        assert not traps, f"cycle {cycle}: hart 1 trapped"
        if int(dut.mem_i_req_valid_o.value) and int(dut.mem_i_req_ready_i.value):
            assert pend["i"] is None or due["i"], "instruction port: two requests in flight"
            pend["i"] = (cycle + lat(), mem.read(int(dut.mem_i_req_addr_o.value), 4))
        elif due["i"]:
            pend["i"] = None
        if int(dut.mem_d_req_valid_o.value) and int(dut.mem_d_req_ready_i.value):
            assert pend["d"] is None or due["d"], "data port: two requests in flight"
            a = int(dut.mem_d_req_addr_o.value)
            assert not (CLINT <= a < CLINT + 0x10000), "a CLINT access went out of the SoC"
            if int(dut.mem_d_req_we_o.value):
                mem.write(a, int(dut.mem_d_req_wdata_o.value), int(dut.mem_d_req_wstrb_o.value), nbytes)
                pend["d"] = (cycle + lat(), 0)
            else:
                pend["d"] = (cycle + lat(), mem.read(a, nbytes))
        elif due["d"]:
            pend["d"] = None
        valid = int(dut.commit_valid_o.value)
        for h in range(NH):
            if (valid >> h) & 1:
                traces[h].append({
                    "pc": (int(dut.commit_pc_o.value) >> (h * X)) & ((1 << X) - 1),
                    "we": (int(dut.commit_we_o.value) >> h) & 1,
                    "rd": (int(dut.commit_rd_o.value) >> (5 * h)) & 31,
                    "data": (int(dut.commit_data_o.value) >> (h * X)) & ((1 << X) - 1)})
        await FallingEdge(dut.clk_i)
    raise AssertionError(f"hart 0 did not finish within {max_cycles} cycles")


def regs(trace):
    r = {}
    for c in trace:
        if c["we"]:
            r[c["rd"]] = c["data"]
    return r


MEMS = ((0.0, 1), (0.3, 2), (0.5, 3))


@cocotb.test()
async def test_harts_and_shared_counter(dut):
    """mhartid differs; both harts add to one word with AMOADD 20 times each, in parallel"""
    w = common.xlen() // 8
    loop = [asm.addi(5, 0, 20), asm.addi(6, 0, 1),
            asm.amo("add", "w", 0, 6, 30), asm.addi(5, 5, -1), asm.bne(5, 0, -8)]
    hart0 = loop + [asm.addi(7, 30, w)] + [asm.lw(8, 0, 7), asm.beq(8, 0, -4)] + [asm.lw(9, 0, 30)]
    hart1 = loop + [asm.addi(7, 30, w), asm.addi(8, 0, 1), asm.sw(8, 0, 7)]
    prog = program(hart0, hart1)
    for stall, seed in MEMS:
        mem, traces, trap = await run_soc(dut, prog, stall, seed)
        assert trap == BASE + 4 * (4 + len(hart0)), f"hart 0 must end at its EBREAK, not {trap:#x}"
        assert regs(traces[0])[1] == 0 and regs(traces[1])[1] == 1, "mhartid"
        assert regs(traces[0])[9] == 40, f"counter {regs(traces[0])[9]} (stall {stall})"
        assert mem.read(BASE + DATA, 4) == 40


@cocotb.test()
async def test_reservation_lost_to_the_other_hart(dut):
    """Hart 1 reserves a word with LR; hart 0 stores to it; hart 1's SC must fail. Then a fresh LR/SC succeeds"""
    w = common.xlen() // 8
    # flags: DATA + w (hart 1 has its reservation), DATA + 2w (hart 0 has stored), DATA + 3w (SC results)
    hart1 = [asm.addi(7, 30, w), asm.addi(8, 30, 2 * w), asm.addi(9, 30, 3 * w), asm.addi(6, 0, 1),
             asm.lr("w", 10, 30), asm.sw(6, 0, 7),                         # reserve, tell hart 0
             asm.lw(11, 0, 8), asm.beq(11, 0, -4),                         # wait for its store
             asm.addi(12, 0, 77), asm.sc("w", 13, 12, 30),                 # must fail
             asm.lr("w", 10, 30), asm.sc("w", 14, 12, 30),                 # must succeed
             asm.sw(13, 0, 9), asm.sw(14, 4, 9), asm.addi(15, 0, 2), asm.sw(15, 0, 7)]
    hart0 = [asm.addi(7, 30, w), asm.addi(8, 30, 2 * w), asm.addi(9, 30, 3 * w),
             asm.lw(11, 0, 7), asm.beq(11, 0, -4),                         # wait for the reservation
             asm.addi(12, 0, 5), asm.sw(12, 0, 30), asm.addi(6, 0, 1), asm.sw(6, 0, 8),
             asm.addi(13, 0, 2), asm.lw(11, 0, 7), asm.bne(11, 13, -4),    # wait for hart 1 to finish
             asm.lw(16, 0, 9), asm.lw(17, 4, 9), asm.lw(18, 0, 30)]
    for stall, seed in MEMS:
        _, traces, _ = await run_soc(dut, program(hart0, hart1), stall, seed)
        r = regs(traces[0])
        assert r[16] == 1, f"the SC after the other hart's store must fail (stall {stall})"
        assert r[17] == 0 and r[18] == 77, "a fresh LR/SC succeeds"


@cocotb.test()
async def test_ipi_and_timers(dut):
    """Hart 0 wakes hart 1 from WFI with msip[1]; then hart 1 waits in WFI for its own timer, which hart 0 sets"""
    w = common.xlen() // 8
    msie, mtie = 1 << 3, 1 << 7
    hart1 = ([asm.addi(7, 30, w)] + li(5, msie | mtie) + [asm.csrw("mie", 5),
             asm.addi(6, 0, 1), asm.sw(6, 0, 7),                           # ready
             asm.WFI,                                                       # until msip[1]
             asm.csrr(10, "mip"), asm.sw(10, 8, 7)] + li(9, CLINT + 4) + [asm.sw(0, 0, 9),   # clear msip[1]
             asm.csrr(11, "mip"), asm.andi(11, 11, msie), asm.bne(11, 0, -8),   # until the line is low
             asm.addi(6, 0, 2), asm.sw(6, 0, 7),                           # woken
             asm.WFI,                                                       # until mtimecmp[1]
             asm.csrr(10, "mip"), asm.sw(10, 16, 7), asm.addi(6, 0, 3), asm.sw(6, 0, 7)])
    hart0 = ([asm.addi(7, 30, w), asm.addi(13, 0, 1)]
             + [asm.lw(11, 0, 7), asm.bne(11, 13, -4)]                     # hart 1 ready
             + li(9, CLINT + 4) + [asm.sw(13, 0, 9)]                       # msip[1] = 1
             + [asm.addi(13, 0, 2), asm.lw(11, 0, 7), asm.bne(11, 13, -4)]
             + li(9, CLINT + 0xBFF8) + [asm.lw(12, 0, 9), asm.addi(12, 12, 100)]
             + li(9, CLINT + 0x4008) + [asm.sw(12, 0, 9), asm.sw(0, 4, 9)]  # mtimecmp[1] = mtime + 100
             + [asm.lw(16, 0, 9)]                                           # read back from the CLINT
             + [asm.addi(13, 0, 3), asm.lw(11, 0, 7), asm.bne(11, 13, -4)]
             + [asm.lw(14, 8, 7), asm.lw(15, 16, 7)])
    for stall, seed in MEMS:
        _, traces, _ = await run_soc(dut, program(hart0, hart1), stall, seed)
        r = regs(traces[0])
        assert r[14] & msie and not r[14] & mtie, f"hart 1 woke on MSIP: mip {r[14]:#x}"
        assert r[15] & mtie, f"hart 1 woke on its timer: mip {r[15]:#x}"
        assert regs(traces[1]).get(1) == 1
        assert r[16] == r[12] and r[12] > 100, f"mtimecmp reads back {r[16]:#x}, written {r[12]:#x}"


@pytest.mark.parametrize("xlen", common.XLENS)
def test_soc_runner(xlen):
    """Build the SoC with two harts (Sol 2) for this XLEN and run the cocotb tests"""
    srcs = [str(common.source(m)) for m in MODULES + ("core_pipe", "rr_arbiter", "amo_unit", "clint")]
    common.run("soc_top", __file__, xlen, extra_sources=srcs,
               build_args=[f"-GNHARTS={NH}"] + [f"-G{k}={v}" for k, v in CONFIGS["sol2"].items()])
