# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
import random

import cocotb
import common
import pytest
from cocotb.triggers import Timer

# funct3 of loads: (name, size in bytes, unsigned, RV64 only)
LOADS = [("LB", 1, False, False), ("LH", 2, False, False), ("LW", 4, False, False),
         ("LD", 8, False, True), ("LBU", 1, True, False), ("LHU", 2, True, False), ("LWU", 4, True, True)]
LOAD_F3 = {"LB": 0, "LH": 1, "LW": 2, "LD": 3, "LBU": 4, "LHU": 5, "LWU": 6}
# funct3 of stores: (name, size in bytes, RV64 only)
STORES = [("SB", 1, False), ("SH", 2, False), ("SW", 4, False), ("SD", 8, True)]


def sizes_available():
    """Access sizes in bytes valid at the current XLEN"""
    return [s for s in (1, 2, 4, 8) if s <= common.xlen() // 8]


def data_values(r):
    """Edge values plus a few random ones"""
    return common.edge_values() + [r.getrandbits(common.xlen()) for _ in range(5)]


async def drive(dut, funct3=0, offset=0, store=0, rdata=0):
    """Drive the inputs and await"""
    dut.funct3_i.value = funct3
    dut.addr_lo_i.value = offset
    dut.store_data_i.value = store
    dut.rdata_i.value = rdata
    await Timer(1, "ns")


def byte_mask(strb):
    """Expand a byte strobe to a bit mask"""
    return sum(0xFF << (8 * i) for i in range(common.xlen() // 8) if (strb >> i) & 1)


@cocotb.test()
async def test_load_aligned(dut):
    """Loads of every size, offset, signedness (aligned only): extension and byte lane"""
    r = random.Random(1)
    for name, size, unsigned, rv64 in LOADS:
        if size not in sizes_available() or (rv64 and common.xlen() == 32):
            continue
        for offset in range(0, common.xlen() // 8, size):
            for rdata in data_values(r):
                await drive(dut, LOAD_F3[name], offset, rdata=rdata)
                field = (rdata >> (8 * offset)) & ((1 << (8 * size)) - 1)
                expected = field if unsigned else common.to_signed(field, 8 * size) & common.mask()
                got = int(dut.load_data_o.value)
                assert got == expected, f"{name} offset {offset} rdata {rdata:#x}: expected {expected:#x}, got {got:#x}"
                assert int(dut.misaligned_o.value) == 0, f"{name} offset {offset} is aligned"


@cocotb.test()
async def test_store_aligned(dut):
    """Stores of every size and offset (aligned only): exact byte strobe and data in its lane"""
    r = random.Random(2)
    for name, size, rv64 in STORES:
        if size not in sizes_available() or (rv64 and common.xlen() == 32):
            continue
        f3 = {"SB": 0, "SH": 1, "SW": 2, "SD": 3}[name]
        for offset in range(0, common.xlen() // 8, size):
            for data in data_values(r):
                await drive(dut, f3, offset, store=data)
                strb = int(dut.wstrb_o.value)
                assert strb == ((1 << size) - 1) << offset, f"{name} offset {offset}: strobe {strb:#x}"
                expected = (data & ((1 << (8 * size)) - 1)) << (8 * offset)
                got = int(dut.wdata_o.value) & byte_mask(strb)
                assert got == expected, f"{name} offset {offset} data {data:#x}: expected {expected:#x}, got {got:#x}"
                assert int(dut.misaligned_o.value) == 0, f"{name} offset {offset} is aligned"


@cocotb.test()
async def test_misaligned_flag(dut):
    """misaligned_o is set exactly when offset is not a multiple of the size"""
    for f3, size in ((0, 1), (1, 2), (2, 4), (3, 8), (4, 1), (5, 2), (6, 4)):
        if size > common.xlen() // 8 or (f3 in (3, 6) and common.xlen() == 32):
            continue
        for offset in range(common.xlen() // 8):
            await drive(dut, f3, offset)
            expected = int(offset % size != 0)
            assert int(dut.misaligned_o.value) == expected, f"funct3 {f3} offset {offset}: expected {expected}"


@cocotb.test()
async def test_load_does_not_depend_on_store_data(dut):
    """Load result depends only on rdata, funct3 and offset; store outputs only on store data, funct3 and offset"""
    r = random.Random(3)
    for _ in range(200):
        f3 = r.choice([f for f in LOAD_F3.values() if f not in (3, 6) or common.xlen() == 64])
        offset = r.randrange(common.xlen() // 8)
        rdata = r.getrandbits(common.xlen())
        await drive(dut, f3, offset, store=0, rdata=rdata)
        first = int(dut.load_data_o.value)
        await drive(dut, f3, offset, store=common.mask(), rdata=rdata)
        assert int(dut.load_data_o.value) == first, "load_data_o changed with store_data_i"


@pytest.mark.parametrize("xlen", common.XLENS)
def test_lsu_runner(xlen):
    """Build the load/store unit for this XLEN and run the cocotb tests"""
    common.run("lsu", __file__, xlen)
