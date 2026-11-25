# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Hazard tests of the pipelined core (ADR 013)

Every program runs on a synchronous BRAM (always ready, response one cycle later), where the
exact cycle counts are checked (one instruction per cycle, one stall per load-use, two cycles per
taken branch), and then on slow memories that stall at random, where only the results are checked:
both ports slow, and a fast instruction port with a slow data port (instructions follow each other
closely while the data port holds them up: the case for operand forwarding while EX waits).
The general programs of test_core.py also run on this core (test_core_pipe_runner)
"""

import asm
import cocotb
import common
import mext
import pytest
from test_core import BASE, MODULES, registers, run_pipe

BRAM = {"stall": 0.0, "extra": 0}
SLOW = {"stall": 0.4, "extra": 3}
SLOW_DATA = {"stall": 0.0, "istall": 0.0, "dstall": 0.6, "extra": 3}


async def both(dut, prog, max_cycles=4000, **kw):
    """Run on BRAM and on the slow memory; the commit traces must be identical. Return the BRAM run"""
    bram = await run_pipe(dut, prog, max_cycles=max_cycles, **BRAM, **kw)
    counters = {k: int(getattr(dut, f"perf_{k}_o").value) for k in ("cycles", "instret", "stalls", "flushes")}
    for seed, mode in ((1, SLOW), (2, SLOW), (3, SLOW_DATA), (4, SLOW_DATA)):
        slow = await run_pipe(dut, prog, max_cycles=max_cycles * 8, seed=seed, **mode, **kw)
        # commit_data_o means nothing without a register write (e.g. CSRW with rd = x0 shows the old CSR)
        for t in (slow[0], bram[0]):
            for c in t:
                if not c["we"]:
                    c["data"] = None
        if slow[0] != bram[0]:
            k = next((i for i, (x, y) in enumerate(zip(slow[0], bram[0], strict=False)) if x != y), min(len(slow[0]), len(bram[0])))
            raise AssertionError(f"the commit trace depends on the memory timing (seed {seed}), entry {k}: "
                                 f"BRAM {bram[0][k] if k < len(bram[0]) else None}, "
                                 f"slow {slow[0][k] if k < len(slow[0]) else None}")
        assert slow[2] == bram[2], "the trap pc depends on the memory timing"
    return bram, counters


def straight(n):
    """n independent ADDIs and ECALL"""
    return [asm.addi(1 + i % 8, 0, i) for i in range(n)] + [asm.ECALL]


@cocotb.test()
async def test_one_instruction_per_cycle(dut):
    """Without hazards the pipeline commits one instruction per cycle: 20 more instructions cost 20 more cycles"""
    (_, _, _), c5 = await both(dut, straight(5))
    (_, _, _), c25 = await both(dut, straight(25))
    assert c25["instret"] - c5["instret"] == 20
    assert c25["cycles"] - c5["cycles"] == 20, "one instruction per cycle"
    assert c25["stalls"] == 0 and c25["flushes"] == 0


@cocotb.test()
async def test_forwarding_without_stalls(dut):
    """A dependent ALU chain gets its operands from MEM, from WB and through the register file (write-through)"""
    prog = [
        asm.addi(1, 0, 3),
        asm.add(2, 1, 1),        # from MEM: 6
        asm.add(3, 2, 1),        # from MEM (x2) and WB (x1): 9
        asm.add(4, 3, 2),        # 15
        asm.addi(5, 0, 1),
        asm.addi(6, 0, 1),
        asm.add(7, 4, 0),        # x4 written three instructions earlier: read in ID while WB writes it
        asm.sub(8, 7, 3),        # 6
        asm.ECALL,
    ]
    (trace, _, _), c = await both(dut, prog)
    r = registers(trace)
    assert (r[2], r[3], r[4], r[7], r[8]) == (6, 9, 15, 15, 6)
    assert c["stalls"] == 0, "forwarding must not stall"


@cocotb.test()
async def test_x0_is_not_forwarded(dut):
    """An instruction that writes x0 must not forward its result"""
    prog = [asm.addi(0, 0, 7), asm.add(1, 0, 0), asm.addi(0, 0, 9), asm.addi(2, 0, 0), asm.add(3, 0, 0), asm.ECALL]
    (trace, _, _), _ = await both(dut, prog)
    r = registers(trace)
    assert r.get(1, 0) == 0 and r.get(3, 0) == 0 and 0 not in r


@cocotb.test()
async def test_load_use_costs_one_cycle(dut):
    """A load followed by its consumer costs one cycle; one instruction in between hides it"""
    def prog(gap):
        return [asm.lui(5, 1), asm.addi(6, 0, 77), asm.sd(6, 0, 5) if common.xlen() == 64 else asm.sw(6, 0, 5)] + \
            [asm.ld(1, 0, 5) if common.xlen() == 64 else asm.lw(1, 0, 5)] + \
            [asm.addi(9, 0, 1)] * gap + [asm.add(2, 1, 1), asm.ECALL]
    (trace0, _, _), c0 = await both(dut, prog(0))
    (trace1, _, _), c1 = await both(dut, prog(1))
    assert registers(trace0)[2] == 154 and registers(trace1)[2] == 154
    assert c0["stalls"] == 1, "load-use: one cycle"
    assert c1["stalls"] == 0, "an instruction between load and use hides the latency"
    assert c1["cycles"] == c0["cycles"], "the stall cycle and the extra instruction cost the same"


@cocotb.test()
async def test_load_into_branch_store_and_jalr(dut):
    """Load-use into a branch condition, store data, a load address and a JALR target"""
    xl = common.xlen() == 64
    ld = asm.ld if xl else asm.lw
    sd = asm.sd if xl else asm.sw
    prog = [
        asm.lui(5, 1),              # 0: x5 = data area
        asm.addi(6, 0, 5),          # 1
        sd(6, 0, 5),                # 2: [x5] = 5
        ld(1, 0, 5),                # 3
        asm.beq(1, 6, 8),           # 4: load-use into a branch: taken, skips 5
        asm.addi(10, 0, 1),         # 5: skipped
        ld(2, 0, 5),                # 6
        sd(2, 8, 5),                # 7: load-use into store data: [x5 + 8] = 5
        ld(3, 8, 5),                # 8: store then load of the same address
        asm.addi(4, 3, 0),          # 9: 5
        asm.auipc(7, 0),            # 10: x7 = BASE + 40
        sd(7, 16, 5),               # 11
        ld(8, 16, 5),               # 12
        asm.jalr(11, 24, 8),        # 13: load-use into a JALR target: x8 + 24 = BASE + 64, index 16
        asm.addi(12, 0, 1),         # 14: skipped
        asm.addi(13, 0, 1),         # 15: skipped
        asm.addi(14, 0, 14),        # 16
        asm.ECALL,                  # 17
    ]
    (trace, _, trap_pc), _ = await both(dut, prog)
    r = registers(trace)
    assert 10 not in r and 12 not in r and 13 not in r
    assert r[4] == 5 and r[3] == 5 and r[14] == 14
    assert r[11] == BASE + 4 * 14, "JALR link"
    assert trap_pc == BASE + 4 * 17


@cocotb.test()
async def test_taken_branch_costs_two_cycles(dut):
    """A loop: every taken branch flushes two instructions"""
    prog = [
        asm.addi(1, 0, 0),
        asm.addi(2, 0, 6),
        asm.addi(1, 1, 1),        # loop
        asm.bne(1, 2, -4),
        asm.ECALL,
    ]
    (trace, _, _), c = await both(dut, prog)
    assert registers(trace)[1] == 6
    assert c["flushes"] == 5, "BNE taken 5 times"
    (_, _, _), base = await both(dut, straight(5))
    # a straight program of 5 + ECALL instructions sets the fill cost; each taken branch adds 2
    assert c["cycles"] - c["instret"] == (base["cycles"] - base["instret"]) + 2 * c["flushes"]


@cocotb.test()
async def test_muldiv_in_the_pipeline(dut):
    """M instructions back to back and their results forwarded, with loads and branches around them"""
    prog = [
        asm.addi(1, 0, 7),
        asm.addi(2, 0, -3),
        asm.mul(3, 1, 2),           # -21
        asm.add(4, 3, 3),           # forwarded M result: -42
        asm.div(5, 4, 1),           # -6, back to back with the M result
        asm.rem(6, 4, 2),           # 0
        asm.mulh(7, 2, 2),          # 0 (9 >> XLEN)
        asm.beq(5, 5, 8),           # taken, skips the next
        asm.addi(8, 0, 1),
        asm.mul(9, 5, 5),           # 36
        asm.ECALL,
    ]
    (trace, _, _), _ = await both(dut, prog, max_cycles=6000)
    m = common.mask()
    r = registers(trace)
    assert r[3] == (-21) & m and r[4] == (-42) & m and r[5] == (-6) & m
    assert r[6] == 0 and r[7] == 0 and r[9] == 36 and 8 not in r
    assert r[5] == mext.expected(4, (-42) & m, 7)


@cocotb.test()
async def test_trap_stops_younger_instructions(dut):
    """Nothing after a trapping instruction is written: no register, no store"""
    sd = asm.sd if common.xlen() == 64 else asm.sw
    prog = [asm.lui(5, 1), asm.addi(6, 0, 9), 0x00000000, sd(6, 0, 5), asm.addi(7, 0, 7)]
    (trace, mem, trap_pc), _ = await both(dut, prog)
    assert trap_pc == BASE + 8
    assert 7 not in registers(trace)
    assert mem.read(BASE + 0x1000, 4) == 0, "the store after the trap must not reach memory"
    # a misaligned load traps in MEM: the store behind it must not go out either
    ld = asm.ld if common.xlen() == 64 else asm.lw
    prog = [asm.lui(5, 1), asm.addi(6, 0, 9), ld(1, 4 if common.xlen() == 64 else 2, 5), sd(6, 0, 5), asm.ECALL]
    (trace, mem, trap_pc), _ = await both(dut, prog)
    assert trap_pc == BASE + 8 and 1 not in registers(trace)
    assert mem.read(BASE + 0x1000, 4) == 0


@cocotb.test()
async def test_newest_value_wins(dut):
    """Two instructions in a row write one register: the reader gets the younger value (MEM before WB)"""
    prog = [asm.addi(1, 0, 1), asm.addi(1, 0, 2), asm.add(2, 1, 1), asm.add(3, 0, 1), asm.ECALL]
    (trace, _, _), _ = await both(dut, prog)
    r = registers(trace)
    assert r[2] == 4 and r[3] == 2


@cocotb.test()
async def test_operand_kept_while_waiting(dut):
    """
    A producer, a load, a consumer of the producer: while the load waits for the data port, the
    producer leaves WB, and the consumer in EX must keep the value it forwarded
    """
    xl = common.xlen() == 64
    ld = asm.ld if xl else asm.lw
    prog = [asm.lui(5, 1)]
    for k in range(6):
        prog += [asm.addi(1, 0, 10 + k), ld(2, 0, 5), asm.add(3 + k, 1, 1), asm.add(9 + k, 1, 0)]
    prog += [asm.ECALL]
    (trace, _, _), _ = await both(dut, prog)
    r = registers(trace)
    for k in range(6):
        assert r[3 + k] == 2 * (10 + k) and r[9 + k] == 10 + k


@cocotb.test()
async def test_load_into_muldiv(dut):
    """A load feeding the multiplier/divider: the unit must not start before the data is there"""
    xl = common.xlen() == 64
    ld, sd = (asm.ld, asm.sd) if xl else (asm.lw, asm.sw)
    prog = [asm.lui(5, 1), asm.addi(6, 0, 12), sd(6, 0, 5), asm.addi(7, 0, 5), sd(7, 8, 5)]
    for k in range(3):
        prog += [ld(1, 0, 5), asm.mul(10 + k, 1, 1), ld(2, 8, 5), asm.div(13 + k, 1, 2)]
    prog += [asm.ECALL]
    (trace, _, _), _ = await both(dut, prog, max_cycles=8000)
    r = registers(trace)
    for k in range(3):
        assert r[10 + k] == 144 and r[13 + k] == 2


@cocotb.test()
async def test_misaligned_jump_does_not_redirect(dut):
    """A jump that traps on its target does not fetch there and is not counted as a flush"""
    prog = [asm.addi(1, 0, 1), asm.jal(2, 6), asm.addi(3, 0, 3), asm.addi(4, 0, 4)]
    (trace, _, trap_pc), c = await both(dut, prog)
    assert trap_pc == BASE + 4 and registers(trace) == {1: 1}
    assert c["flushes"] == 0


@pytest.mark.parametrize("xlen", common.XLENS)
def test_pipeline_runner(xlen):
    """Build the pipelined core for this XLEN and run the hazard tests"""
    common.run("core_pipe", __file__, xlen, extra_sources=[str(common.REPO / f"rtl/core/{m}.sv") for m in MODULES])
