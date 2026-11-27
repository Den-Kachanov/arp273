# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Interrupts of the pipelined core (ADR 019): software, timer and external, WFI, priority, masking

The core sees the interrupt lines of a model of the CLINT and of one external source (`Devices`). Each
program runs on a BRAM and on four randomly stalling memories. Where an interrupt lands depends on the
timing, so the commit traces are not compared with each other; instead every run must show that the
interrupts are precise:
  - the main program (everything below the handler) commits the same instructions with the same
    results as a run where interrupts are never enabled: nothing is skipped, repeated or changed
  - every interrupt enters the handler with the expected mcause, mtval = 0, and mepc = the pc of the
    first main instruction committed after the handler returns
"""

import asm
import cocotb
import common
import pytest
from test_core import BASE, CONFIGS, MODULES, config_run, registers, run_pipe
from test_csr import HANDLER, handler_reads, program
from test_pipeline import BRAM, SLOW, SLOW_DATA, both

CLINT = 0x0200_0000
EXT = 0x0C00_0000          # test device: a store writes the external interrupt line (bit 0)
MSIE, MTIE, MEIE = 1 << 3, 1 << 7, 1 << 11
MSI, MTI, MEI = 3, 7, 11


class Devices:
    """
    The CLINT of one hart at CLINT (msip at +0, mtimecmp at +0x4000, mtime at +0xBFF8, as rtl/soc/clint.sv)
    and an external interrupt line, which a store to EXT sets or clears, or which rises by itself at
    cycle `meip_at`. mtime counts cycles from reset. The lines are registered: they follow the state
    one cycle later, as in the RTL
    """

    def __init__(self, meip_at=None):
        self.mtime, self.mtimecmp, self.msip, self.meip = 0, (1 << 64) - 1, 0, 0
        self.cycle, self.meip_at = 0, meip_at
        self.out = (0, 0, 0)

    def owns(self, addr):
        return CLINT <= addr < CLINT + 0x10000 or addr == EXT or addr == EXT + 4

    def _byte(self, a):
        if a == CLINT:
            return self.msip
        if CLINT + 0x4000 <= a < CLINT + 0x4008:
            return (self.mtimecmp >> (8 * (a - CLINT - 0x4000))) & 0xFF
        if CLINT + 0xBFF8 <= a < CLINT + 0xC000:
            return (self.mtime >> (8 * (a - CLINT - 0xBFF8))) & 0xFF
        return 0

    def _write(self, a, b):
        if a == CLINT:
            self.msip = b & 1
        elif CLINT + 0x4000 <= a < CLINT + 0x4008:
            k = 8 * (a - CLINT - 0x4000)
            self.mtimecmp = (self.mtimecmp & ~(0xFF << k)) | (b << k)
        elif CLINT + 0xBFF8 <= a < CLINT + 0xC000:
            k = 8 * (a - CLINT - 0xBFF8)
            self.mtime = (self.mtime & ~(0xFF << k)) | (b << k)
        elif a == EXT:
            self.meip = b & 1

    def access(self, addr, we, wdata, wstrb, nbytes):
        data = sum(self._byte(addr + k) << (8 * k) for k in range(nbytes))
        if we:
            for k in range(nbytes):
                if (wstrb >> k) & 1:
                    self._write(addr + k, (wdata >> (8 * k)) & 0xFF)
            return 0
        return data

    def lines(self):
        return self.out

    def tick(self):
        if self.meip_at is not None and self.cycle == self.meip_at:
            self.meip = 1
        self.cycle += 1
        self.mtime += 1
        self.out = (self.msip, int(self.mtime >= self.mtimecmp), self.meip)


def li(rd, value):
    """rd = value for 0 <= value < 2^31 (LUI + ADDI)"""
    hi = (value + 0x800) >> 12
    return [asm.lui(rd, hi & 0xFFFFF), asm.addi(rd, rd, value - (hi << 12))]


def store64(rs, off, base):
    """store a 64-bit value: SD, or two SW at XLEN = 32 (the upper half is rs's sign)"""
    if common.xlen() == 64:
        return [asm.sd(rs, off, base)]
    return [asm.sw(rs, off, base), asm.srai(13, rs, 31), asm.sw(13, off + 4, base)]


def irq_handler():
    """Records mcause (x10), mepc (x11), mtval (x12), counts in x20, silences every source, returns to mepc"""
    return ([asm.csrr(10, "mcause"), asm.csrr(11, "mepc"), asm.csrr(12, "mtval"), asm.addi(20, 20, 1)]
            + li(14, CLINT) + [asm.sw(0, 0, 14)]                            # msip = 0
            + [asm.lui(15, 4), asm.add(15, 14, 15), asm.addi(13, 0, -1)]
            + store64(13, 0, 15)                                             # mtimecmp = all ones
            + li(14, EXT) + [asm.sw(0, 0, 14), asm.MRET])                    # external line = 0


def main_loop(n=12):
    """A loop that touches every kind of instruction an interrupt may hit: ALU, loads, stores, an AMO, M"""
    return [
        asm.auipc(7, 1),                    # x7: data (pc + 0x1000)
        asm.addi(5, 0, 0),                  # registers start with random values
        asm.addi(9, 0, 0),
        asm.addi(6, 0, n),
        asm.addi(5, 5, 1),                  # loop
        asm.sw(5, 0, 7),
        asm.lw(8, 0, 7),
        asm.add(9, 9, 8),
        asm.mul(16, 5, 5),
        asm.amo("add", "w", 17, 5, 7),
        asm.divu(18, 16, 5),
        asm.addi(6, 6, -1),
        asm.bne(6, 0, -32),                 # to the loop
    ]


def build(setup, enable, tail=None):
    """program(setup + enable + main loop + EBREAK) with the interrupt handler; enable = [] for the reference"""
    body = setup + enable + main_loop() + (tail or []) + [asm.EBREAK]
    return program(body, handler=irq_handler())


ENABLE = [asm.csrrsi(0, "mstatus", 8)]      # MIE


def main_part(trace, volatile=()):
    """the main program's commits: (pc, rd, data); data of registers in `volatile` is not compared"""
    out = []
    for c in trace:
        if c.get("trap") or c["pc"] >= BASE + 4 * HANDLER:
            continue
        rd = c["rd"] if c["we"] else 0
        out.append((c["pc"], rd, None if rd in volatile else (c["data"] if c["we"] else None)))
    return out


async def check(dut, setup, causes, *, meip_at=None, tail=None, volatile=()):
    """
    Run with interrupts enabled on every memory model and check that they are precise (module docstring);
    `causes`: the expected mcause codes, in order. Return the trace of the BRAM run
    """
    x = common.xlen()
    ref_prog, first = build(setup, [asm.NOP] * len(ENABLE), tail)
    prog, _ = build(setup, ENABLE, tail)
    # the reference sees the same interrupt lines, but MIE is never set
    ref, _, _ = await run_pipe(dut, ref_prog, max_cycles=6000, devices=Devices(meip_at), **BRAM)
    assert not any(c.get("trap") for c in ref), "the reference run takes no interrupt"
    expected = main_part(ref, volatile)
    out = None
    for seed, mode in ((0, BRAM), (1, SLOW), (2, SLOW), (3, SLOW_DATA), (4, SLOW_DATA)):
        kw = dict(mode, seed=seed) if seed else dict(mode)
        trace, _, trap_pc = await run_pipe(dut, prog, max_cycles=40000, skip_traps=len(causes),
                                           devices=Devices(meip_at), **kw)
        where = f"seed {seed}"
        assert trap_pc == BASE + 4 * (first + len(setup) + len(ENABLE) + len(main_loop()) + len(tail or [])), \
            f"{where}: the run must end at the final EBREAK"
        got_main = main_part(trace, volatile)
        if got_main != expected:
            k = next((i for i, (g, e) in enumerate(zip(got_main, expected, strict=False)) if g != e),
                     min(len(got_main), len(expected)))
            raise AssertionError(f"{where}: the main program changed under interrupts at commit {k}: "
                                 f"got {[(hex(p), r, d) for p, r, d in got_main[k:k + 3]]}, "
                                 f"expected {[(hex(p), r, d) for p, r, d in expected[k:k + 3]]}")
        got = handler_reads(trace, regs=(10, 11, 12))
        assert [g[0] for g in got] == [(1 << (x - 1)) | c for c in causes], \
            f"{where}: mcause {[hex(g[0]) for g in got]}"
        traps = [i for i, c in enumerate(trace) if c.get("trap")]
        for (_, mepc, mtval), i in zip(got, traps, strict=True):
            assert mtval == 0, f"{where}: mtval of an interrupt must be 0"
            assert trace[i]["pc"] == mepc, f"{where}: the interrupted instruction is not mepc"
            after = next(c for c in trace[i + 1:] if c["pc"] < BASE + 4 * HANDLER)
            assert after["pc"] == mepc, f"{where}: after MRET the core must resume at mepc"
        out = out or trace
    return out


def setup_mie(bits):
    return li(1, bits) + [asm.csrw("mie", 1)]


@cocotb.test()
async def test_software_interrupt(dut):
    """msip = 1 raises MSI at once; the handler clears it; the loop around it is not disturbed"""
    setup = setup_mie(MSIE) + li(2, CLINT) + [asm.addi(3, 0, 1), asm.sw(3, 0, 2)]
    await check(dut, setup, [MSI])


@cocotb.test()
async def test_timer_interrupt(dut):
    """mtimecmp = 60: the timer fires in the middle of the loop (cycle 60 on every memory)"""
    setup = setup_mie(MTIE) + li(2, CLINT + 0x4000) + [asm.addi(3, 0, 60)] + store64(3, 0, 2)
    await check(dut, setup, [MTI])


@cocotb.test()
async def test_external_interrupt(dut):
    """The external line rises at a fixed cycle, independent of the program"""
    await check(dut, setup_mie(MEIE), [MEI], meip_at=50)


@cocotb.test()
async def test_priority(dut):
    """All three pending at once: MEI first (the handler silences all); then MSI and MTI together: MSI first"""
    raise_all = (li(2, CLINT) + [asm.addi(3, 0, 1), asm.sw(3, 0, 2)]           # msip
                 + li(2, CLINT + 0x4000) + store64(0, 0, 2)                      # mtimecmp = 0
                 + li(2, EXT) + [asm.sw(3, 0, 2)])                               # external
    await check(dut, setup_mie(MSIE | MTIE | MEIE) + raise_all, [MEI])
    raise_two = (li(2, CLINT) + [asm.addi(3, 0, 1), asm.sw(3, 0, 2)]
                 + li(2, CLINT + 0x4000) + store64(0, 0, 2))
    await check(dut, setup_mie(MSIE | MTIE | MEIE) + raise_two, [MSI])


@cocotb.test()
async def test_memory_access_is_not_replayed(dut):
    """
    The interrupt rises while stores and an AMO pass WB: none of them may be replaced (their requests have
    gone out). The handler writes a marker over the stored word; if a store ran again after MRET it would
    overwrite the marker, and an AMO would add twice
    """
    handler = ([asm.csrr(10, "mcause"), asm.csrr(11, "mepc"), asm.csrr(12, "mtval"), asm.addi(20, 20, 1)]
               + li(14, CLINT) + [asm.sw(0, 0, 14), asm.addi(13, 0, 0x55), asm.sw(13, 0, 7), asm.MRET])
    body = (setup_mie(MSIE) + li(2, CLINT) + [
        asm.auipc(7, 1), asm.addi(9, 7, 8), asm.addi(3, 0, 1), asm.addi(4, 0, 9), asm.sw(0, 8, 7),
    ] + ENABLE + [
        asm.sw(3, 0, 2),                    # msip = 1: the interrupt rises from here on
        asm.sw(4, 0, 7),
        asm.amo("add", "w", 5, 4, 9),       # x5 = 0, word 2 = 9
        asm.sw(4, 0, 7),
        asm.sw(4, 0, 7),
        asm.amo("add", "w", 6, 4, 9),       # x6 = 9, word 2 = 18
        asm.sw(4, 0, 7),
        asm.addi(8, 0, 1),                  # the first instruction that may take it
        asm.lw(16, 0, 7),                   # the marker
        asm.lw(17, 8, 7),
        asm.EBREAK,
    ])
    prog, first = program(body, handler=handler)
    (trace, _, _), _ = await both(dut, prog, skip_traps=1, devices_factory=Devices)
    r = registers(trace)
    assert handler_reads(trace)[0][1] == BASE + 4 * (first + body.index(asm.addi(8, 0, 1))), \
        "the interrupt is taken on the first instruction that is not a memory access"
    assert r[16] == 0x55, f"a store ran again after the handler: {r[16]:#x}"
    assert r[17] == 18 and r[5] == 0 and r[6] == 9, "each AMO executes once"


@cocotb.test()
async def test_masked(dut):
    """Pending but not enabled (mie bit clear, or MIE clear): no interrupt; mip shows the lines, ignores writes"""
    prog, _ = program(setup_mie(MTIE) + li(2, CLINT) + [asm.addi(3, 0, 1), asm.sw(3, 0, 2)]   # MSIP, not enabled
                      + ENABLE + [asm.NOP] * 4 + [asm.csrr(6, "mip")]
                      + [asm.csrrci(0, "mstatus", 8)]                                          # MIE clear, then
                      + li(2, CLINT + 0x4000) + store64(0, 0, 2) + [asm.NOP] * 4                # MTIP, enabled in mie
                      + [asm.csrr(4, "mip"), asm.csrw("mip", 0), asm.csrr(5, "mip"), asm.EBREAK],
                      handler=irq_handler())
    trace, _, _ = await run_pipe(dut, prog, max_cycles=4000, devices=Devices(), **BRAM)
    r = registers(trace)
    assert not any(c.get("trap") for c in trace), "a masked interrupt must not be taken"
    assert r[6] == 1 << 3, f"mip with MSIP only: {r[6]:#x}"
    assert r[4] == (1 << 3) | (1 << 7) and r[5] == r[4], f"mip {r[4]:#x} {r[5]:#x}"


@cocotb.test()
async def test_wfi(dut):
    """WFI waits for an enabled interrupt: with MIE clear it then just continues; with MIE set the handler
    runs after it (mepc = WFI + 4); the wait lasts until mtime reaches mtimecmp (300 cycles)"""
    # mtimecmp = mtime + 300 (mtime < 2^31 here)
    wait = (li(2, CLINT + 0xBFF8) + [asm.lw(3, 0, 2), asm.addi(3, 3, 300)] + li(2, CLINT + 0x4000)
            + store64(3, 0, 2) + [asm.csrr(21, "mcycle"), asm.WFI, asm.csrr(22, "mcycle")])
    # MIE clear: no handler, the WFI ends when mtime reaches mtimecmp
    prog, _ = program(setup_mie(MTIE) + wait + [asm.EBREAK], handler=irq_handler())
    trace, _, _ = await run_pipe(dut, prog, max_cycles=4000, devices=Devices(), **BRAM)
    r = registers(trace)
    assert not any(c.get("trap") for c in trace)
    assert r[22] - r[21] >= 280, f"WFI did not wait: mcycle {r[21]} -> {r[22]}"
    # MIE set: the interrupt arrives during the wait; WFI commits once, the handler returns after it
    trace = await check(dut, setup_mie(MTIE), [MTI], tail=wait, volatile=(3, 21, 22))
    wfi_pc = next(c["pc"] for c in trace if c["inst"] == asm.WFI)
    assert [c["pc"] for c in trace].count(wfi_pc) == 1, "WFI commits exactly once"
    assert handler_reads(trace)[0][1] == wfi_pc + 4, "mepc = WFI + 4"


@pytest.mark.parametrize("xlen", common.XLENS)
@pytest.mark.parametrize("config", CONFIGS)
def test_irq_runner(xlen, config):
    """Build the pipelined core in every configuration for this XLEN and run the interrupt tests"""
    config_run("core_pipe", __file__, xlen, config, [str(common.source(m)) for m in MODULES])
