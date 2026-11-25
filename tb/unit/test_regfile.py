# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov

import random

import cocotb
import common
import pytest
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


async def settle():
    await Timer(1, "ns")


async def write_reg(dut, addr, data):
    """Write data into a register on the rising clock edge"""
    dut.we_i.value = 1
    dut.waddr_i.value = addr
    dut.wdata_i.value = data

    await settle()
    await RisingEdge(dut.clk_i)

    dut.we_i.value = 0
    await settle()


async def read_regs(dut, addr1, addr2):
    """Read both ports and return their values"""
    dut.raddr1_i.value = addr1
    dut.raddr2_i.value = addr2

    await settle()

    return (
        int(dut.rdata1_o.value),
        int(dut.rdata2_o.value)
    )


@cocotb.test()
async def test_initial_values(dut):
    """x0 always reads zero; initialize all other registers"""

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())

    dut.we_i.value = 0
    dut.waddr_i.value = 0
    dut.wdata_i.value = 0
    dut.raddr1_i.value = 0
    dut.raddr2_i.value = 0

    await settle()

    r1, r2 = await read_regs(dut, 0, 0)

    assert r1 == 0
    assert r2 == 0

    # Initialize registers before reading them.
    for addr in range(1, 32):
        await write_reg(dut, addr, 0)

    for addr in range(1, 32):
        r1, _ = await read_regs(dut, addr, 0)
        assert r1 == 0, f"x{addr} should contain 0"


@cocotb.test()
async def test_write_read_all_registers(dut):
    """Write and read all 31 writable registers"""

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())

    dut.we_i.value = 0
    dut.waddr_i.value = 0
    dut.wdata_i.value = 0
    dut.raddr1_i.value = 0
    dut.raddr2_i.value = 0

    await settle()

    expected = {}

    for addr in range(1, 32):
        data = (addr * 0x123456789ABCDEF) & common.mask()
        expected[addr] = data
        await write_reg(dut, addr, data)

    for addr in range(1, 32):
        r1, r2 = await read_regs(dut, addr, 0)

        assert r1 == expected[addr], (
            f"x{addr}: expected {hex(expected[addr])}, "
            f"got {hex(r1)}"
        )

        assert r2 == 0


@cocotb.test()
async def test_independent_read_ports(dut):
    """Both read ports must access different registers independently"""

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())

    dut.we_i.value = 0
    dut.waddr_i.value = 0
    dut.wdata_i.value = 0

    await settle()

    expected = {}

    for addr in range(1, 32):
        data = (1 << (addr - 1)) & common.mask()
        expected[addr] = data
        await write_reg(dut, addr, data)

    for addr1 in range(32):
        for addr2 in range(32):
            r1, r2 = await read_regs(dut, addr1, addr2)

            exp1 = expected.get(addr1, 0)
            exp2 = expected.get(addr2, 0)

            assert r1 == exp1, (
                f"Port 1 failed: x{addr1}, "
                f"expected {hex(exp1)}, got {hex(r1)}"
            )

            assert r2 == exp2, (
                f"Port 2 failed: x{addr2}, "
                f"expected {hex(exp2)}, got {hex(r2)}"
            )


@cocotb.test()
async def test_x0_write_protection(dut):
    """Writing to x0 must never change its read value"""

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())

    dut.raddr1_i.value = 0
    dut.raddr2_i.value = 0

    for data in common.edge_values():
        dut.we_i.value = 1
        dut.waddr_i.value = 0
        dut.wdata_i.value = data

        await settle()

        r1, r2 = await read_regs(dut, 0, 0)

        assert r1 == 0
        assert r2 == 0

        await RisingEdge(dut.clk_i)
        await settle()

        r1, r2 = await read_regs(dut, 0, 0)

        assert r1 == 0
        assert r2 == 0

    dut.we_i.value = 0


@cocotb.test()
async def test_write_through(dut):
    """Read-after-write forwarding must work before the clock edge"""

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())

    dut.we_i.value = 0
    dut.waddr_i.value = 0
    dut.wdata_i.value = 0

    await settle()

    for addr in range(1, 32):
        data = (addr * 0xABCDEF123456789) & common.mask()

        dut.we_i.value = 1
        dut.waddr_i.value = addr
        dut.wdata_i.value = data

        # Check forwarding before the rising edge.
        r1, r2 = await read_regs(dut, addr, addr)

        assert r1 == data, (
            f"Port 1 forwarding failed for x{addr}: "
            f"expected {hex(data)}, got {hex(r1)}"
        )

        assert r2 == data, (
            f"Port 2 forwarding failed for x{addr}: "
            f"expected {hex(data)}, got {hex(r2)}"
        )

        await RisingEdge(dut.clk_i)
        await settle()

        dut.we_i.value = 0

        r1, r2 = await read_regs(dut, addr, addr)

        assert r1 == data
        assert r2 == data


@cocotb.test()
async def test_x0_priority_over_forwarding(dut):
    """Reading x0 must return zero even when writing to x0"""

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())

    dut.we_i.value = 1
    dut.waddr_i.value = 0
    dut.wdata_i.value = common.mask()

    for addr in range(32):
        r1, r2 = await read_regs(dut, 0, addr)

        assert r1 == 0, "x0 must have priority over forwarding"

        if addr == 0:
            assert r2 == 0

    dut.we_i.value = 0


@cocotb.test()
async def test_write_disabled(dut):
    """No register should change when we_i is low"""

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())

    dut.we_i.value = 0
    dut.waddr_i.value = 0
    dut.wdata_i.value = 0

    await settle()

    expected = {}

    for addr in range(1, 32):
        data = (addr * 0x111111111111111) & common.mask()
        expected[addr] = data
        await write_reg(dut, addr, data)

    dut.we_i.value = 0

    for addr in range(1, 32):
        dut.waddr_i.value = addr
        dut.wdata_i.value = common.mask()

        await RisingEdge(dut.clk_i)
        await settle()

        r1, _ = await read_regs(dut, addr, 0)

        assert r1 == expected[addr], (
            f"x{addr} changed with we_i=0"
        )


@cocotb.test()
async def test_random_operations(dut):
    """Random writes and reads against a Python reference model"""

    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())

    r = random.Random(1)
    expected = [0] * 32

    dut.we_i.value = 0
    dut.waddr_i.value = 0
    dut.wdata_i.value = 0

    await settle()

    # No reset (ADR 005): initialize registers before the model reads them
    for addr in range(1, 32):
        await write_reg(dut, addr, 0)

    for _ in range(500):
        we = r.randint(0, 1)
        waddr = r.randint(0, 31)
        wdata = r.getrandbits(common.xlen())

        raddr1 = r.randint(0, 31)
        raddr2 = r.randint(0, 31)

        dut.we_i.value = we
        dut.waddr_i.value = waddr
        dut.wdata_i.value = wdata
        dut.raddr1_i.value = raddr1
        dut.raddr2_i.value = raddr2

        await settle()

        if raddr1 == 0:
            exp1 = 0
        elif we and waddr == raddr1:
            exp1 = wdata
        else:
            exp1 = expected[raddr1]

        if raddr2 == 0:
            exp2 = 0
        elif we and waddr == raddr2:
            exp2 = wdata
        else:
            exp2 = expected[raddr2]

        r1 = int(dut.rdata1_o.value)
        r2 = int(dut.rdata2_o.value)

        assert r1 == exp1, (
            f"Port 1 failed: raddr={raddr1}, "
            f"expected {hex(exp1)}, got {hex(r1)}"
        )

        assert r2 == exp2, (
            f"Port 2 failed: raddr={raddr2}, "
            f"expected {hex(exp2)}, got {hex(r2)}"
        )

        await RisingEdge(dut.clk_i)

        if we and waddr != 0:
            expected[waddr] = wdata

        await settle()


@pytest.mark.parametrize("xlen", common.XLENS)
def test_regfile_runner(xlen):
    """Build the register file for this XLEN and run cocotb tests"""

    common.run("regfile", __file__, xlen)
