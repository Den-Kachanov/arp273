# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
CSRs and exceptions of the pipelined core (ADR 014)

Each program installs a handler at a fixed address (mtvec) and runs on a BRAM and on slow memories
(test_pipeline.both): the commit traces, including the exceptions taken, must be identical.
The standard handler records mcause, mepc and mtval (they appear in the commit trace), counts
the exception in x20, and returns to the instruction after the trapping one. Every program ends
with an EBREAK that is not handled: it ends the run
"""

import asm
import cocotb
import common
import pytest
from test_core import BASE, CONFIGS, MODULES, config_run, registers, run_pipe
from test_pipeline import both

HANDLER = 64          # word index of the handler

STD_HANDLER = [
    asm.csrr(10, "mcause"),
    asm.csrr(11, "mepc"),
    asm.csrr(12, "mtval"),
    asm.addi(20, 20, 1),
    asm.addi(13, 11, 4),
    asm.csrw("mepc", 13),
    asm.MRET,
]


def program(body, handler=STD_HANDLER):
    """mtvec = the handler; x20 = 0; then body, padded with NOPs up to the handler"""
    words = [asm.auipc(31, 0), asm.addi(31, 31, 4 * HANDLER), asm.csrw("mtvec", 31), asm.addi(20, 0, 0)]
    first = len(words)
    words += body
    assert len(words) <= HANDLER
    words += [asm.NOP] * (HANDLER - len(words)) + handler
    return words, first


def handler_reads(trace, regs=(10, 11, 12)):
    """For every pass through the handler: the values its first CSR reads put into regs"""
    out = []
    for i, c in enumerate(trace):
        if c["pc"] == BASE + 4 * HANDLER and c["we"]:
            got = {}
            for k in trace[i:i + len(regs) + 2]:
                if k["we"] and k["rd"] in regs and k["rd"] not in got:
                    got[k["rd"]] = k["data"]
            out.append(tuple(got.get(r) for r in regs))
    return out


@cocotb.test()
async def test_exception_causes(dut):
    """Every exception: mcause, mepc and mtval, no effect of the trapping instruction, execution resumes after it"""
    m = common.mask()
    bad_op = asm.r_type(0b0000010, 3, 2, 0, 1, asm.OPC["OP"])
    body = [
        asm.lui(5, 1),                 # x5 = 0x1000
        asm.addi(6, 0, 5),
        asm.ECALL,                     # 11, mtval 0
        asm.EBREAK,                    # 3, mtval = pc
        bad_op,                        # 2, mtval = instruction
        asm.lw(7, 2, 5),               # 4, mtval = address
        asm.sw(6, 1, 5),               # 6, mtval = address; nothing stored
        asm.jal(8, 6),                 # 0, mtval = target; no link
        asm.csrr(9, "satp"),           # 2: no such CSR
        asm.csrw("mvendorid", 6),      # 2: read-only CSR
        asm.csrr(9, "mvendorid"),      # legal: reads 0
        asm.addi(6, 6, 1),
        asm.EBREAK,
    ]
    prog, first = program(body)
    pc = lambda k: BASE + 4 * (first + k)
    (trace, mem, trap_pc), _ = await both(dut, prog, skip_traps=8)
    expected = [
        (11, pc(2), 0),
        (3, pc(3), pc(3)),
        (2, pc(4), bad_op),
        (4, pc(5), 0x1002),
        (6, pc(6), 0x1001),
        (0, pc(7), (pc(7) + 6) & m),
        (2, pc(8), body[8]),
        (2, pc(9), body[9]),
    ]
    got = handler_reads(trace)
    assert len(got) == 8
    for k, (e, g) in enumerate(zip(expected, got, strict=True)):
        assert g == e, f"exception {k}: (mcause, mepc, mtval) expected {[hex(v) for v in e]}, got {[hex(v) for v in g]}"
    r = registers(trace)
    assert r[20] == 8, "eight exceptions handled"
    assert 7 not in r and 8 not in r, "a trapping instruction writes no register"
    assert r[9] == 0 and r[6] == 6
    assert mem.read(0x1000, 4) == 0, "a misaligned store must not reach memory"
    assert trap_pc == pc(12)
    assert [c["pc"] for c in trace if c.get("trap")] == [e[1] for e in expected], "the exceptions in program order"


@cocotb.test()
async def test_csr_read_modify_write(dut):
    """CSRRW/S/C and the immediate forms return the old value; reads feed the next instruction at once"""
    m = common.mask()
    x = common.xlen()
    misa = ((2 if x == 64 else 1) << (x - 2)) | (1 << 0) | (1 << 8) | (1 << 12)
    body = [
        asm.addi(1, 0, 0x0F0), asm.addi(3, 0, 0x00F), asm.addi(5, 0, 0x0F0),
        asm.csrw("mscratch", 1),
        asm.csrrs(2, "mscratch", 3),     # 0xF0 -> 0xFF
        asm.csrrc(4, "mscratch", 5),     # 0xFF -> 0x0F
        asm.csrrwi(6, "mscratch", 21),   # 0x0F -> 21
        asm.csrrsi(7, "mscratch", 8),    # 21 -> 29
        asm.csrrci(8, "mscratch", 1),    # 29 -> 28
        asm.csrr(9, "mscratch"),
        asm.addi(10, 9, 1),              # needs x9 at once: waits for the CSR read in WB
        asm.add(11, 9, 10),
        asm.csrr(12, "misa"),
        asm.csrrs(13, "mvendorid", 0),   # set with x0: a read, legal on a read-only CSR
        asm.csrrsi(14, "mhartid", 0),
        asm.addi(15, 0, -1),
        asm.csrw("misa", 15),            # WARL: ignored
        asm.csrr(16, "misa"),
        asm.addi(17, 31, 7),
        asm.csrw("mtvec", 17),
        asm.csrr(18, "mtvec"),           # direct mode only: bits 1:0 read 0
        asm.csrw("mepc", 17),
        asm.csrr(19, "mepc"),            # bits 1:0 read 0
        asm.csrw("mip", 15),
        asm.csrr(21, "mip"),
        asm.csrw("mie", 15),
        asm.csrr(22, "mie"),             # MSIE, MTIE, MEIE
        asm.addi(23, 0, 0x80),
        asm.csrw("mie", 23),
        asm.csrr(24, "mie"),             # MTIE alone stays in its place
        asm.EBREAK,
    ]
    prog, _ = program(body)
    (trace, _, _), _ = await both(dut, prog)
    r = registers(trace)
    handler = BASE + 4 * HANDLER
    assert (r[2], r[4], r[6], r[7], r[8], r[9]) == (0xF0, 0xFF, 0x0F, 21, 29, 28)
    assert r[10] == 29 and r[11] == 57, "CSR read forwarded to the next instructions"
    assert r[12] == misa and r[16] == misa
    assert r[13] == 0 and r[14] == 0
    assert r[18] == (handler + 4) & m and r[19] == (handler + 4) & m
    assert r[21] == 0 and r[22] == 0x888 and r[24] == 0x80
    assert r[20] == 0, "no exception"


@cocotb.test()
async def test_mstatus_on_trap_and_mret(dut):
    """A trap saves MIE in MPIE and clears MIE; MRET restores MIE from MPIE and sets MPIE; MPP reads M"""
    handler = [asm.csrr(10, "mstatus"), asm.csrr(11, "mepc"), asm.addi(11, 11, 4), asm.csrw("mepc", 11), asm.MRET]
    body = [
        asm.csrrsi(0, "mstatus", 8),     # MIE = 1
        asm.csrr(1, "mstatus"),
        asm.ECALL,
        asm.csrr(2, "mstatus"),
        asm.csrrci(0, "mstatus", 8),     # MIE = 0 (MPIE stays 1)
        asm.ECALL,
        asm.csrr(3, "mstatus"),
        asm.EBREAK,
    ]
    prog, _ = program(body, handler)
    (trace, _, _), _ = await both(dut, prog, skip_traps=2)
    r = registers(trace)
    mpp = 0x1800
    assert r[1] == mpp | 0x8
    assert [g[0] for g in handler_reads(trace, regs=(10,))] == [mpp | 0x80, mpp], "MPIE = MIE at the trap"
    assert r[2] == mpp | 0x88, "MRET: MIE = MPIE, MPIE = 1"
    assert r[3] == mpp | 0x80


@cocotb.test()
async def test_mret_jumps_to_mepc(dut):
    """MRET without a trap goes to mepc; the instructions after it are dropped"""
    body = [
        asm.auipc(1, 0),
        asm.addi(1, 1, 20),              # the auipc + 20: addi x3
        asm.csrw("mepc", 1),
        asm.MRET,
        asm.addi(2, 0, 2),               # skipped
        asm.addi(3, 0, 3),               # mepc
        asm.EBREAK,
    ]
    prog, _ = program(body)
    (trace, _, _), _ = await both(dut, prog)
    r = registers(trace)
    assert 2 not in r and r[3] == 3


@cocotb.test()
async def test_nothing_after_mret_or_fence_i_reaches_memory(dut):
    """A store right behind MRET is on the dropped path: it must not be sent while MRET redirects in WB"""
    body = [
        asm.lui(5, 1), asm.addi(6, 0, 77),
        asm.auipc(1, 0),
        asm.addi(1, 1, 20),              # the auipc + 20: addi x3
        asm.csrw("mepc", 1),
        asm.MRET,
        asm.sw(6, 0, 5),                 # dropped
        asm.addi(3, 0, 3),               # mepc
        asm.FENCE_I,
        asm.sw(6, 8, 5),                 # fetched again after FENCE.I: stored once
        asm.EBREAK,
    ]
    prog, _ = program(body)
    (trace, mem, _), _ = await both(dut, prog)
    assert mem.read(0x1000, 4) == 0, "the store after MRET must not reach memory"
    assert mem.read(0x1008, 4) == 77
    assert registers(trace)[3] == 3


@cocotb.test()
async def test_trap_aborts_the_divider(dut):
    """An exception older than a division in progress stops the divider; the handler and the code after it divide correctly"""
    m = common.mask()
    handler = [
        asm.addi(16, 0, 100), asm.addi(17, 0, 7), asm.divu(15, 16, 17),
        asm.csrr(11, "mepc"), asm.addi(11, 11, 4), asm.csrw("mepc", 11), asm.MRET,
    ]
    body = [
        asm.addi(1, 0, -1), asm.addi(2, 0, 3),
        asm.NOP,                         # (was WFI, a no-op before interrupts; now WFI waits, ADR 019)
        0x00000000,                      # illegal: reaches WB while the division below runs in EX
        asm.divu(3, 1, 2),
        asm.divu(4, 1, 2),
        asm.EBREAK,
    ]
    prog, first = program(body, handler)
    (trace, _, trap_pc), _ = await both(dut, prog, skip_traps=1, max_cycles=6000)
    r = registers(trace)
    assert r[15] == 14
    assert r[3] == m // 3 and r[4] == m // 3
    assert trap_pc == BASE + 4 * (first + 6)
    assert [c["pc"] for c in trace].count(BASE + 4 * (first + 4)) == 1, "the division commits once"


@cocotb.test()
async def test_fence_i_fetches_again(dut):
    """FENCE.I makes an earlier store visible to instruction fetch: the overwritten instruction runs"""
    new = asm.addi(7, 0, 42)
    hi, lo = (new + 0x800) >> 12, new & 0xFFF
    lo = lo - 0x1000 if lo >= 0x800 else lo
    body = [
        asm.auipc(1, 0),
        asm.lui(2, hi),
        asm.addi(2, 2, lo),
        asm.sw(2, 20, 1),                # overwrite the instruction at auipc + 20
        asm.FENCE_I,
        asm.addi(7, 0, 1),               # replaced by addi x7, x0, 42
        asm.EBREAK,
    ]
    prog, _ = program(body)
    (trace, _, _), _ = await both(dut, prog)
    assert registers(trace)[7] == 42


@cocotb.test()
async def test_counters_readable(dut):
    """minstret counts the instructions retired before the reading one; mcycle grows; cycle and instret are copies"""
    body = [
        asm.csrr(1, "minstret"),
        asm.NOP, asm.NOP, asm.NOP, asm.NOP, asm.NOP,
        asm.csrr(2, "minstret"),
        asm.csrr(3, "mcycle"),
        asm.csrr(4, "cycle"),
        asm.csrr(5, "instret"),
        asm.EBREAK,
    ]
    prog, _ = program(body)
    # mcycle depends on the memory timing: each memory is checked on its own, not by comparing traces
    for kw in ({"stall": 0.0, "extra": 0}, {"stall": 0.4, "extra": 3, "seed": 1}):
        trace, _, _ = await run_pipe(dut, prog, **kw)
        r = registers(trace)
        assert r[2] - r[1] == 6
        assert r[5] - r[2] == 3
        assert r[4] > r[3] > 0


@cocotb.test()
async def test_stale_btb_after_fence_i(dut):
    """
    A taken branch is learned, then overwritten with a NOP (store, FENCE.I) and reached again by a
    sequential fetch, so a predictor would guess "taken" from a stale entry: the NOP must fall through,
    and the instruction behind it must run (ADR 018)
    """
    nop = asm.NOP
    body = [
        asm.auipc(5, 0),                 # 0: x5 = address of word 0
        asm.addi(7, 0, 0),               # 1: the register file has no reset
        asm.addi(9, 0, 0),               # 2
        asm.addi(6, 0, 2),               # 3
        asm.NOP,                         # 4: landing pad, so that P is fetched sequentially
        asm.beq(0, 0, 8),                # 5: P, taken: skips word 6
        asm.addi(7, 7, 100),             # 6: runs only once P is a NOP
        asm.addi(6, 6, -1),              # 7
        asm.bne(6, 0, -12),              # 8: back to P
        asm.bne(9, 0, 28),               # 9: second pass done: to word 16
        asm.addi(9, 0, 1),               # 10
        asm.addi(8, 0, nop),             # 11: x8 = the NOP encoding (0x13)
        asm.sw(8, 20, 5),                # 12: overwrite P
        asm.FENCE_I,                     # 13
        asm.addi(6, 0, 1),               # 14
        asm.jal(0, -44),                 # 15: to word 4
        asm.EBREAK,                      # 16
    ]
    assert nop == 0x13
    prog, _ = program(body)
    (trace, _, _), _ = await both(dut, prog)
    assert registers(trace).get(7) == 100, "the overwritten branch must fall through to the ADDI"


@cocotb.test()
async def test_trap_on_predicted_path(dut):
    """
    A branch learned as taken jumps to an ECALL: the exception and the MRET back happen on the
    predicted path, three times; every memory timing gives the same trace
    """
    body = [
        asm.addi(6, 0, 3),               # 0
        asm.NOP,                         # 1: loop; P is then fetched sequentially
        asm.beq(0, 0, 8),                # 2: P, taken: to the ECALL
        asm.addi(7, 0, 100),             # 3: never runs
        asm.ECALL,                       # 4: the handler returns to word 5
        asm.addi(6, 6, -1),              # 5
        asm.bne(6, 0, -20),              # 6: to word 1
        asm.EBREAK,
    ]
    prog, first = program(body)
    (trace, _, _), _ = await both(dut, prog, skip_traps=3)
    r = registers(trace)
    assert r[20] == 3 and 7 not in r
    assert [m[:2] for m in handler_reads(trace)] == [(11, BASE + 4 * (first + 4))] * 3


@cocotb.test()
async def test_dropped_branch_not_learned(dut):
    """
    FENCE.I commits while the branch two instructions behind it leaves EX: that branch is dropped and
    fetched again, so it must not train the predictor. Its first real run is then a first sight: 2
    flushes in both cores (FENCE.I, and the taken branch, which Sol 2 does not know yet)
    """
    body = [
        asm.FENCE_I,
        asm.NOP,
        asm.beq(0, 0, 8),                # taken: skips the ADDI
        asm.addi(7, 0, 1),
        asm.EBREAK,
    ]
    prog, _ = program(body)
    (trace, _, _), c = await both(dut, prog)
    assert 7 not in registers(trace)
    assert c["flushes"] == 2


@cocotb.test()
async def test_misaligned_jump_traps_every_time(dut):
    """
    A JALR to an address with bit 1 set traps (cause 0) every time it runs, also once the predictor has
    learned it (it is learned harmlessly: the BTB drops target bits 1:0, and the trap drops the path)
    """
    body = [
        asm.auipc(5, 0),                 # 0
        asm.addi(6, 0, 2),               # 1
        asm.NOP,                         # 2: loop
        asm.jalr(0, 2, 5),               # 3: target x5 + 2, misaligned: trap, the handler returns to word 4
        asm.addi(6, 6, -1),              # 4
        asm.bne(6, 0, -12),              # 5: to word 2
        asm.EBREAK,
    ]
    prog, first = program(body)
    (trace, _, _), _ = await both(dut, prog, skip_traps=2)
    jump = BASE + 4 * (first + 3)
    target = BASE + 4 * first + 2
    assert handler_reads(trace) == [(0, jump, target)] * 2


@pytest.mark.parametrize("xlen", common.XLENS)
@pytest.mark.parametrize("config", CONFIGS)
def test_csr_runner(xlen, config):
    """Build the pipelined core (Sol 1 and Sol 2) for this XLEN and run the CSR and exception tests"""
    config_run("core_pipe", __file__, xlen, config, [str(common.source(m)) for m in MODULES])
