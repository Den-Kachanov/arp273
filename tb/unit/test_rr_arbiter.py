# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
The round-robin arbiter (rtl/soc/rr_arbiter.sv): three masters with random requests share one port whose
memory stalls and answers 1 to 3 cycles late. Checked: one request in flight; each request goes down
exactly once with its payload and the id of its master; its response comes back to that master only;
a master that keeps requesting is served within N grants of others (round robin); a request may be
granted in the cycle the previous response arrives
"""

import random

import cocotb
import common
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

N, W, RW = 3, 16, 12


async def setup(dut, clock=True):
    """reset (and start the clock); a run can end with a request in flight, so every run starts from reset"""
    if clock:
        cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_ni.value = 0
    for s in (dut.req_valid_i, dut.req_data_i, dut.req_ready_i, dut.rsp_valid_i, dut.rsp_data_i):
        s.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1


async def run(dut, rng, cycles, load, stall, latency):
    """random traffic; returns the grants in order and how often the port took a request right after a response"""
    want = [None] * N                        # payload each master offers
    waiting = [None] * N                     # response each master waits for
    pending = None                           # (cycle, data, master)
    grants, back_to_back = [], 0
    skipped = [0] * N                        # grants to others while this master was requesting
    for cycle in range(cycles):
        for m in range(N):
            if want[m] is None and waiting[m] is None and rng.random() < load:
                want[m] = rng.getrandbits(W)
        dut.req_valid_i.value = sum(1 << m for m in range(N) if want[m] is not None)
        dut.req_data_i.value = sum((want[m] or 0) << (m * W) for m in range(N))
        dut.req_ready_i.value = int(rng.random() >= stall)
        due = pending is not None and pending[0] == cycle
        dut.rsp_valid_i.value = int(due)
        dut.rsp_data_i.value = pending[1] if (due and pending is not None) else 0
        await Timer(1, "ns")

        rv = int(dut.rsp_valid_o.value)
        if due and pending is not None:
            assert rv == 1 << pending[2], f"cycle {cycle}: response for master {pending[2]} went to {rv:#b}"
            assert int(dut.rsp_data_o.value) == pending[1]
            m = pending[2]
            assert waiting[m] == pending[1]
            waiting[m] = None
        else:
            assert rv == 0, f"cycle {cycle}: a response without one from below"
        ready = int(dut.req_ready_o.value)
        if int(dut.req_valid_o.value) and int(dut.req_ready_i.value):
            assert pending is None or due, f"cycle {cycle}: a second request in flight"
            m = int(dut.req_id_o.value)
            assert want[m] is not None, f"cycle {cycle}: granted master {m}, which does not request"
            assert int(dut.req_data_o.value) == want[m], "the payload of the granted master"
            assert ready == 1 << m, f"cycle {cycle}: ready {ready:#b}, granted {m}"
            back_to_back += int(due)
            data = rng.getrandbits(RW)
            pending = (cycle + 1 + rng.randrange(latency), data, m)
            waiting[m] = data
            want[m] = None
            grants.append(m)
            for k in range(N):
                if k != m and want[k] is not None:
                    skipped[k] += 1
                    assert skipped[k] < N, f"cycle {cycle}: master {k} passed over {skipped[k]} times"
            skipped[m] = 0
        else:
            assert ready == 0, f"cycle {cycle}: ready {ready:#b} without a grant"
            if due:
                pending = None
        await FallingEdge(dut.clk_i)
    return grants, back_to_back


@cocotb.test()
async def test_random_traffic(dut):
    """Heavy and light load, stalling memory: routing, one in flight, fairness"""
    await setup(dut)
    rng = random.Random(3)
    grants, b2b = await run(dut, rng, 3000, load=0.9, stall=0.3, latency=3)
    assert set(grants) == set(range(N)) and len(grants) > 500
    assert b2b > 50, "a new request must be granted in the cycle the previous response arrives"
    await setup(dut, clock=False)
    grants, _ = await run(dut, rng, 2000, load=0.1, stall=0.0, latency=1)
    assert set(grants) == set(range(N))


@cocotb.test()
async def test_round_robin_order(dut):
    """All masters always requesting, a memory that answers in one cycle: grants rotate 0, 1, 2, 0, ..."""
    await setup(dut)
    grants, _ = await run(dut, random.Random(1), 200, load=1.0, stall=0.0, latency=1)
    assert len(grants) > 60
    start = grants[0]
    assert grants == [(start + k) % N for k in range(len(grants))], f"grants {grants[:12]}"


@pytest.mark.parametrize("xlen", common.XLENS)
def test_rr_arbiter_runner(xlen):
    """Build the arbiter for three masters and run the cocotb tests"""
    common.run("rr_arbiter", __file__, xlen, build_args=[f"-GN={N}", f"-GW={W}", f"-GRW={RW}"])
