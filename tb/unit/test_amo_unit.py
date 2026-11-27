# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
The atomics unit (rtl/soc/amo_unit.sv, ADR 019) against a reference model, on random traffic from three
harts to four words (so that reservations collide), with a memory below that stalls and answers late

The reference applies each request at the moment the unit accepts it: one request is in flight at a time,
so that is its place in the order of the memory. Checked: every response, the memory at the end, the
protocol on the memory side (one request in flight, a failed SC sends nothing), and that an AMO's read and
write are not split by another access
"""

import random

import cocotb
import common
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

NHARTS = 3
WORDS = 4
BASE = 0x8000_0000
OPS = {"add": 0, "swap": 1, "lr": 2, "sc": 3, "xor": 4, "or": 8, "and": 12, "min": 16, "max": 20,
       "minu": 24, "maxu": 28}


class Model:
    """memory and reservations as the unit's description says"""

    def __init__(self, nbytes):
        self.nbytes = nbytes
        self.mem = {}
        self.resv = [None] * NHARTS

    def read(self, addr):
        return sum(self.mem.get(addr + k, 0) << (8 * k) for k in range(self.nbytes))

    def write(self, addr, data, strb):
        for k in range(self.nbytes):
            if (strb >> k) & 1:
                self.mem[addr + k] = (data >> (8 * k)) & 0xFF

    def clear_others(self, hart, addr):
        for h in range(NHARTS):
            if h != hart and self.resv[h] is not None and self.resv[h] // self.nbytes == addr // self.nbytes:
                self.resv[h] = None

    def apply(self, hart, kind, addr, wdata, strb, op):
        """the response of one request"""
        lane = (strb & -strb).bit_length() - 1
        n = bin(strb).count("1")
        word = self.read(addr)
        if kind == "load":
            return word
        if kind == "store":
            self.write(addr, wdata, strb)
            self.clear_others(hart, addr)
            return None                      # the memory's store response: not checked
        if op == "lr":
            self.resv[hart] = addr + lane
            return word
        if op == "sc":
            ok = self.resv[hart] == addr + lane
            self.resv[hart] = None
            if ok:
                self.write(addr, wdata, strb)
                self.clear_others(hart, addr)
            return 0 if ok else 1 << (8 * lane)
        m = (1 << (8 * n)) - 1
        a, b = (word >> (8 * lane)) & m, (wdata >> (8 * lane)) & m
        sa, sb = common.to_signed(a, 8 * n), common.to_signed(b, 8 * n)
        r = {"add": a + b, "swap": b, "xor": a ^ b, "or": a | b, "and": a & b, "min": a if sa < sb else b,
             "max": a if sa > sb else b, "minu": min(a, b), "maxu": max(a, b)}[op]
        self.write(addr, (r & m) << (8 * lane), strb)
        self.clear_others(hart, addr)
        return word


def random_request(rng, nbytes):
    """(hart, kind, addr, wdata, strb, op)"""
    hart = rng.randrange(NHARTS)
    addr = BASE + nbytes * rng.randrange(WORDS)
    kind = rng.choice(["load", "store", "amo", "amo", "amo"])
    if nbytes == 8 and rng.random() < 0.5:
        strb = 0xFF
    else:
        strb = 0xF << (4 * rng.randrange(nbytes // 4))
    wdata = rng.choice([0, 1, (1 << 64) - 1, 1 << 31, (1 << 31) - 1, rng.getrandbits(64)]) & ((1 << (8 * nbytes)) - 1)
    op = rng.choice(list(OPS)) if kind == "amo" else None
    if kind == "amo" and rng.random() < 0.4:
        op = rng.choice(["lr", "sc"])        # many LR/SC pairs
    if kind == "store":
        strb = rng.choice([strb, 1 << rng.randrange(nbytes)])
    return hart, kind, addr, wdata, strb, op


@cocotb.test()
async def test_random_traffic(dut):
    """3000 random requests; responses and the final memory match the model"""
    nbytes = common.xlen() // 8
    rng = random.Random(7)
    model = Model(nbytes)
    hw = {}                                  # the memory below the unit
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    for s in (dut.req_valid_i, dut.req_addr_i, dut.req_we_i, dut.req_wstrb_i, dut.req_wdata_i, dut.req_amo_i,
              dut.req_atop_i, dut.req_id_i, dut.mem_req_ready_i, dut.mem_rsp_valid_i, dut.mem_rsp_data_i):
        s.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1

    def hw_read(addr):
        return sum(hw.get(addr + k, 0) << (8 * k) for k in range(nbytes))

    pending = None                           # memory response: (cycle, data)
    cur = None                               # request being offered
    expect = None                            # response the unit owes: (value or None, request)
    sent = 0
    mem_ops_since_accept = []
    for cycle in range(60000):
        if sent >= 3000 and cur is None and expect is None:
            break
        if cur is None and expect is None and sent < 3000 and rng.random() < 0.8:
            cur = random_request(rng, nbytes)
            sent += 1
        if cur:
            hart, kind, addr, wdata, strb, op = cur
            dut.req_valid_i.value = 1
            dut.req_id_i.value = hart
            dut.req_addr_i.value = addr
            dut.req_we_i.value = int(kind != "load" and op != "lr")
            dut.req_wstrb_i.value = strb
            dut.req_wdata_i.value = wdata
            dut.req_amo_i.value = int(kind == "amo")
            dut.req_atop_i.value = OPS[op] if op else 0
        else:
            dut.req_valid_i.value = 0
        dut.mem_req_ready_i.value = int(rng.random() < 0.7)
        due = pending is not None and pending[0] == cycle
        dut.mem_rsp_valid_i.value = int(due)
        dut.mem_rsp_data_i.value = pending[1] if (due and pending is not None) else 0
        await Timer(1, "ns")

        if int(dut.rsp_valid_o.value):
            assert expect is not None, f"cycle {cycle}: a response without a request"
            want, req = expect
            got = int(dut.rsp_data_o.value)
            if want is not None:
                assert got == want, f"cycle {cycle}: {req}: response {got:#x}, expected {want:#x}"
            if req[1] == "amo" and req[5] not in ("lr", "sc"):
                assert mem_ops_since_accept == ["r", "w"], f"{req}: the AMO's accesses were {mem_ops_since_accept}"
            if req[5] == "sc" and want:
                assert mem_ops_since_accept == [], f"{req}: a failed SC went to memory"
            expect = None
        if due:
            pending = None
        if int(dut.mem_req_valid_o.value) and int(dut.mem_req_ready_i.value):
            assert pending is None or due, "memory side: a second request while one is in flight"
            a = int(dut.mem_req_addr_o.value)
            if int(dut.mem_req_we_o.value):
                st, d = int(dut.mem_req_wstrb_o.value), int(dut.mem_req_wdata_o.value)
                for k in range(nbytes):
                    if (st >> k) & 1:
                        hw[a + k] = (d >> (8 * k)) & 0xFF
                # the data of a store's response means nothing (the protocol): send garbage
                pending = (cycle + 1 + rng.randrange(3), rng.getrandbits(8 * nbytes))
                mem_ops_since_accept.append("w")
            else:
                pending = (cycle + 1 + rng.randrange(3), hw_read(a))
                mem_ops_since_accept.append("r")
        if cur and int(dut.req_ready_o.value):
            assert expect is None, "a request accepted before the previous response"
            expect = (model.apply(*cur), cur)
            # the accesses that belong to this request: an access sent in this same cycle is its own
            mem_ops_since_accept = mem_ops_since_accept[-1:] if (int(dut.mem_req_valid_o.value) and
                                                                  int(dut.mem_req_ready_i.value)) else []
            cur = None
        await FallingEdge(dut.clk_i)
    assert sent == 3000 and expect is None, f"stuck after {sent} requests"
    for w in range(WORDS):
        a = BASE + nbytes * w
        assert hw_read(a) == model.read(a), f"memory at {a:#x}: {hw_read(a):#x}, model {model.read(a):#x}"


@cocotb.test()
async def test_successful_sc_clears_other_reservations(dut):
    """Harts 0 and 1 both reserve a word; hart 0's SC succeeds and is a store, so hart 1's SC must fail"""
    nbytes = common.xlen() // 8
    seq = [(1, "amo", BASE, 0, 0xF, "lr"), (0, "amo", BASE, 0, 0xF, "lr"),
           (0, "amo", BASE, 5, 0xF, "sc"), (1, "amo", BASE, 6, 0xF, "sc"), (2, "load", BASE, 0, 0xF, None)]
    got = await drive(dut, seq, nbytes)
    assert got[2] == 0 and got[3] == 1, f"SC results {got[2]}, {got[3]}"
    assert got[4] & 0xFFFFFFFF == 5


async def drive(dut, seq, nbytes):
    """run a fixed sequence of requests, one after another, on an always-ready memory; return the responses"""
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    for sig in (dut.req_valid_i, dut.req_amo_i, dut.mem_req_ready_i, dut.mem_rsp_valid_i):
        sig.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1
    dut.mem_req_ready_i.value = 1
    hw, out = {}, []
    state = {"pending": None}

    async def cycle():
        """one clock: the memory answers last cycle's request, takes this cycle's; returns whether a request was accepted"""
        p = state["pending"]
        dut.mem_rsp_valid_i.value = int(p is not None)
        dut.mem_rsp_data_i.value = p if p is not None else 0
        await Timer(1, "ns")
        if int(dut.rsp_valid_o.value):
            out.append(int(dut.rsp_data_o.value))
        state["pending"] = None
        if int(dut.mem_req_valid_o.value):
            a = int(dut.mem_req_addr_o.value)
            if int(dut.mem_req_we_o.value):
                for k in range(nbytes):
                    if (int(dut.mem_req_wstrb_o.value) >> k) & 1:
                        hw[a + k] = (int(dut.mem_req_wdata_o.value) >> (8 * k)) & 0xFF
                state["pending"] = 0x5A5A          # a store's response data means nothing
            else:
                state["pending"] = sum(hw.get(a + k, 0) << (8 * k) for k in range(nbytes))
        accepted = int(dut.req_valid_i.value) and int(dut.req_ready_o.value)
        await FallingEdge(dut.clk_i)
        return accepted

    for n, (hart, kind, addr, wdata, strb, op) in enumerate(seq):
        dut.req_valid_i.value = 1
        dut.req_id_i.value = hart
        dut.req_addr_i.value = addr
        dut.req_we_i.value = int(kind != "load" and op != "lr")
        dut.req_wstrb_i.value = strb
        dut.req_wdata_i.value = wdata
        dut.req_amo_i.value = int(kind == "amo")
        dut.req_atop_i.value = OPS[op] if op else 0
        while not await cycle():
            pass
        dut.req_valid_i.value = 0
        while len(out) < n + 1:
            await cycle()
    return out


@pytest.mark.parametrize("xlen", common.XLENS)
def test_amo_unit_runner(xlen):
    """Build the atomics unit for three harts and run the cocotb tests"""
    common.run("amo_unit", __file__, xlen, build_args=[f"-GNHARTS={NHARTS}"])
