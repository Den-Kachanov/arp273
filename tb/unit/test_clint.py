# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
CLINT (rtl/soc/clint.sv, ADR 019): mtime, mtimecmp and msip of every hart, the interrupt lines, and the
register port (valid/ready, one response a cycle later, byte strobes). Built with NHARTS = 2; XLEN = 64
reaches the 64-bit registers in one access, XLEN = 32 in two halves
"""

import cocotb
import common
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

MSIP, MTIMECMP, MTIME = 0x0000, 0x4000, 0xBFF8
NHARTS = 2


async def reset(dut, tick=1):
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    dut.tick_i.value = tick
    dut.req_valid_i.value = 0
    dut.req_addr_i.value = 0
    dut.req_we_i.value = 0
    dut.req_wstrb_i.value = 0
    dut.req_wdata_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1


async def access(dut, addr, we=0, data=0, strb=None):
    """one request in the cycle after the current falling edge; returns the response data"""
    nbytes = common.xlen() // 8
    assert int(dut.req_ready_o.value) == 1, "the CLINT is always ready"
    dut.req_valid_i.value = 1
    dut.req_addr_i.value = addr & ~(nbytes - 1)
    dut.req_we_i.value = we
    dut.req_wstrb_i.value = ((1 << nbytes) - 1) if strb is None else strb
    dut.req_wdata_i.value = data
    await FallingEdge(dut.clk_i)
    dut.req_valid_i.value = 0
    dut.req_we_i.value = 0
    assert int(dut.rsp_valid_o.value) == 1, "the response comes one cycle after the request"
    rsp = int(dut.rsp_data_o.value)
    return rsp


async def read64(dut, addr):
    if common.xlen() == 64:
        return await access(dut, addr)
    lo = await access(dut, addr)
    hi = await access(dut, addr + 4)
    return lo | (hi << 32)


async def write64(dut, addr, value):
    if common.xlen() == 64:
        await access(dut, addr, 1, value)
    else:
        await access(dut, addr, 1, value & 0xFFFFFFFF)
        await access(dut, addr + 4, 1, value >> 32)


async def idle(dut, n):
    for _ in range(n):
        await FallingEdge(dut.clk_i)
        assert int(dut.rsp_valid_o.value) == 0, "no response without a request"


@cocotb.test()
async def test_reset_and_count(dut):
    """After reset: mtime counts every cycle, mtimecmp is all ones, no interrupt line is set"""
    await reset(dut)
    await idle(dut, 3)
    t0 = await read64(dut, MTIME)
    await idle(dut, 10)
    t1 = await read64(dut, MTIME)
    assert t1 - t0 == 10 + (2 if common.xlen() == 32 else 1), f"mtime {t0} -> {t1}"
    for h in range(NHARTS):
        assert await read64(dut, MTIMECMP + 8 * h) == (1 << 64) - 1
        assert await access(dut, MSIP + 4 * h) & 1 == 0
    assert int(dut.mtip_o.value) == 0 and int(dut.msip_o.value) == 0


@cocotb.test()
async def test_tick_gates_the_count(dut):
    """mtime counts only on tick_i"""
    await reset(dut, tick=0)
    t0 = await read64(dut, MTIME)
    await idle(dut, 20)
    assert await read64(dut, MTIME) == t0


@cocotb.test()
async def test_timer_interrupt(dut):
    """MTIP of a hart is mtime >= its mtimecmp, one cycle late; the other hart is not affected"""
    await reset(dut, tick=0)
    await write64(dut, MTIME, 100)
    await write64(dut, MTIMECMP + 8, 105)          # hart 1
    await FallingEdge(dut.clk_i)
    assert int(dut.mtip_o.value) == 0
    dut.tick_i.value = 1
    seen = None
    for cycle in range(12):
        await FallingEdge(dut.clk_i)
        if int(dut.mtip_o.value) and seen is None:
            seen = cycle
    assert int(dut.mtip_o.value) == 0b10, f"only hart 1: mtip {int(dut.mtip_o.value):#b}"
    assert seen is not None
    dut.tick_i.value = 0
    await write64(dut, MTIMECMP + 8, (1 << 64) - 1)   # silence it
    await idle(dut, 2)
    assert int(dut.mtip_o.value) == 0
    await write64(dut, MTIMECMP, 0)                   # hart 0: 0 <= mtime at once
    await idle(dut, 2)
    assert int(dut.mtip_o.value) == 0b01


@cocotb.test()
async def test_mtime_compare_is_unsigned_64(dut):
    """The comparison is unsigned on 64 bits: mtimecmp = 2^63 is far in the future"""
    await reset(dut, tick=0)
    await write64(dut, MTIME, (1 << 63) - 1)
    await write64(dut, MTIMECMP, 1 << 63)
    await idle(dut, 2)
    assert int(dut.mtip_o.value) & 1 == 0
    await write64(dut, MTIME, 1 << 63)
    await idle(dut, 2)
    assert int(dut.mtip_o.value) & 1 == 1


@cocotb.test()
async def test_software_interrupt(dut):
    """msip bit 0 of each hart drives its line; other bits are not stored"""
    await reset(dut)
    await access(dut, MSIP + 4, 1, 0xFFFFFFFF << (32 if common.xlen() == 64 else 0),
                 strb=0xF0 if common.xlen() == 64 else 0xF)
    await idle(dut, 2)
    assert int(dut.msip_o.value) == 0b10
    got = await access(dut, MSIP + 4)
    lane = (got >> 32) if common.xlen() == 64 else got
    assert lane == 1, f"msip reads {lane:#x}"
    await access(dut, MSIP, 1, 1, strb=0x1)
    await idle(dut, 2)
    assert int(dut.msip_o.value) == 0b11
    await access(dut, MSIP + 4, 1, 0, strb=0xF0 if common.xlen() == 64 else 0xF)
    await idle(dut, 2)
    assert int(dut.msip_o.value) == 0b01
    await access(dut, MSIP, 1, 0xFE, strb=0x1)        # bit 0 clear, the others set: cleared
    await idle(dut, 2)
    assert int(dut.msip_o.value) == 0b00


@cocotb.test()
async def test_byte_strobes(dut):
    """A write changes only the bytes its strobes select"""
    await reset(dut, tick=0)
    await write64(dut, MTIMECMP, 0x1122334455667788)
    nbytes = common.xlen() // 8
    rsp = await access(dut, MTIMECMP, 1, 0xAA << 8, strb=0b10)     # byte 1 only
    assert rsp == 0, "a write responds with 0"
    want = 0x112233445566AA88
    assert await read64(dut, MTIMECMP) == want
    if nbytes == 4:
        await access(dut, MTIMECMP + 4, 1, 0xBB << 24, strb=0b1000)
        want = 0xBB2233445566AA88
        assert await read64(dut, MTIMECMP) == want


@cocotb.test()
async def test_write_wins_and_read_before_write(dut):
    """A write to mtime wins over the increment; the next read sees the written value (plus the cycles since)"""
    await reset(dut)
    await write64(dut, MTIME, 1000)
    t = await read64(dut, MTIME)
    assert 1000 <= t <= 1003, f"mtime after the write: {t}"


@cocotb.test()
async def test_no_write_without_valid(dut):
    """Write enable, strobes and data without valid change nothing"""
    await reset(dut, tick=0)
    await write64(dut, MTIMECMP, 1234)
    dut.req_addr_i.value = MTIMECMP
    dut.req_we_i.value = 1
    dut.req_wstrb_i.value = (1 << (common.xlen() // 8)) - 1
    dut.req_wdata_i.value = 0
    await idle(dut, 3)
    dut.req_we_i.value = 0
    assert await read64(dut, MTIMECMP) == 1234


@cocotb.test()
async def test_unmapped_reads_zero(dut):
    """Addresses of nothing read 0 and ignore writes"""
    await reset(dut, tick=0)
    for addr in (0x0008, 0x3FF8, 0x4000 + 8 * NHARTS, 0x8000, 0xBFF0):
        await access(dut, addr, 1, (1 << common.xlen()) - 1)
        assert await access(dut, addr) == 0, f"{addr:#x}"
    assert int(dut.msip_o.value) == 0 and int(dut.mtip_o.value) == 0


@pytest.mark.parametrize("xlen", common.XLENS)
def test_clint_runner(xlen):
    """Build the CLINT with two harts for this XLEN and run the cocotb tests"""
    common.run("clint", __file__, xlen, build_args=[f"-GNHARTS={NHARTS}"])
