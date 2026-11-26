# Sol 1

The first generation of Sol: the five-stage pipeline of stage 1, released as **`v0.1.0`** (RTL of
commit `3c7be96`). Today it is the configuration `sol1` of `rtl/sol/core_pipe.sv` (`MUL_BITS = 1`,
`BP = 0`), cycle for cycle the same core; `git checkout v0.1.0` gives its code exactly as released.

## What it is

- **Pipeline:** IF with a 2-entry fetch queue, ID, EX, MEM, WB ([ADR 013](../../adr/013-pipeline-and-memory-interface.md)).
  Forwarding from MEM and WB; a load or CSR read used by the next instruction costs one cycle; branches
  and jumps are decided in EX and cost two cycles when taken.
- **Multiplier/divider:** one iterative unit, one bit per cycle, with a short path for operands that fit
  in half a word ([ADR 009](../../adr/009-muldiv-iterative.md), [ADR 011](../../adr/011-muldiv-short-path.md)):
  `MULW` takes 34 cycles, a full-width multiplication 66.
- **Machine mode:** CSRs, precise exceptions in WB, ECALL, EBREAK, MRET, FENCE.I ([ADR 014](../../adr/014-csr-and-exceptions.md)).
- **Verification:** riscv-tests `rv64ui-p` and `rv64um-p` 66/66, commit log equal to Spike on C and random
  programs, mutation-tested unit tests ([verification](../../verification.md)).

## Measurements

2026-11-26, release build, simulation on a one-cycle BRAM, RTL `3c7be96` (results:
`docs/results/sol1.json`, `docs/results/sol1-energy.json`).

| | |
|---|---|
| CoreMark/MHz | 1.360 (CPI 2.23 on CoreMark) |
| Dhrystone | 0.706 DMIPS/MHz (with the plain byte loops of `sw/lib`) |
| CPI of the measured part, 29 benchmarks | 1.07 (`rsort`, `nettle-sha256`) to 6.96 (`edn`) |
| Energy per CoreMark iteration | 1.127 × 10¹⁰ units ([ADR 016](../../adr/016-energy-estimate.md)), half of it the clock of the flip-flops |
| Area, XLEN = 64 | 5802 LUT6, 3741 flip-flops; 127 320 transistors (CMOS estimate) |
| Logic delay, xc7 | 5650 ps (no routing; not Fmax) |

## What limits it

On CoreMark, of the 4.07 million cycles above one per instruction:

| Cause | Share |
|---|---|
| `MULW` in the iterative multiplier (94 thousand, about 33 cycles each) | 76 % |
| Taken branches and jumps (2 cycles each) | 18 % |
| Load-use (1 cycle each) | 5 % |

This is what [Sol 2](sol2.md) attacks.
