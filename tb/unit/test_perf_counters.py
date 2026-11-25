# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
import cocotb
import common
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer


async def reset(dut):
    """Start the clock and hold reset for two cycles"""
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    dut.commit_i.value = 0
    dut.stall_i.value = 0
    dut.flush_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1


async def read(dut):
    """Return (cycles, instret, stalls, flushes)"""
    await Timer(1, "ns")
    return tuple(int(s.value) for s in (dut.cycles_o, dut.instret_o, dut.stalls_o, dut.flushes_o))


@cocotb.test()
async def test_counts_events(dut):
    """Every counter counts its own event; cycles count every cycle after reset"""
    await reset(dut)
    assert await read(dut) == (0, 0, 0, 0)

    pattern = [(1, 0, 0), (1, 0, 1), (0, 1, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0)]
    for commit, stall, flush in pattern:
        dut.commit_i.value = commit
        dut.stall_i.value = stall
        dut.flush_i.value = flush
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
    assert await read(dut) == (6, 3, 2, 1)


@cocotb.test()
async def test_reset_clears(dut):
    """A reset in the middle of a run clears all counters"""
    await reset(dut)
    dut.commit_i.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    assert (await read(dut))[1] == 5
    dut.rst_ni.value = 0
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    assert await read(dut) == (0, 0, 0, 0)


@cocotb.test()
async def test_64_bit_width(dut):
    """Counters are 64 bits wide whatever XLEN is"""
    assert len(dut.cycles_o) == 64


@pytest.mark.parametrize("xlen", common.XLENS)
def test_perf_counters_runner(xlen):
    """Build the counters for this XLEN and run the cocotb tests"""
    common.run("perf_counters", __file__, xlen)
