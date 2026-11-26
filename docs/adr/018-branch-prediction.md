# ADR 018: Branch prediction in fetch: built, measured, not enabled

## Context

Sol 1 decides branches in EX and pays two cycles for every taken branch, JAL and JALR (ADR 013). With
the faster multiplier of Sol 2 (ADR 017), these flushes are the largest remaining cost on CoreMark: 18 %
of its cycles above one per instruction on Sol 1. A trace model of the design ([design of Sol 2](../design/sol2.md))
estimated that a small predictor removes most of them and that a full-size BTB is not needed. A change
is accepted only if it lowers energy × time per CoreMark iteration (ADR 016, ADR 017).

## Options

| Option | Pros | Cons |
|---|---|---|
| A. No prediction (Sol 1) | Smallest | 2 cycles per taken transfer |
| B. Static "backward taken" in ID | No tables | A predicted-taken branch still costs a cycle (the target is known only in ID); model: no better than A + a BHT |
| **C. BHT of 2-bit counters + small BTB + return stack, looked up in IF** | A correct prediction costs nothing; with the 4-bit multiplier the model gave CoreMark/MHz 2.18–2.21 for BTB 8–32 | Flip-flops for the tables, clocked every cycle; the lookup must stay off the redirect path |
| D. gshare (global history) | Better on correlated branches | The model showed no gain over C on CoreMark |

## Decision

Option C was built and verified: `bpred` ([modules/bpred.md](../modules/bpred.md)) in IF of
`core_pipe`, parameter `BP`. **It is not enabled by default (`BP = 0`)**: measured, it does not lower
energy × time per CoreMark iteration (below). Sol 2 is therefore the four-bit multiplier of ADR 017
alone; the predictor stays a parameter, tested in every run, for a later version that removes its long
path. How it works when enabled ([modules/core_pipe.md](../modules/core_pipe.md)):

- **Lookup at `pc_f_q` only.** The fetch at an EX redirect target is not predicted, so the BTB lookup
  is not in series with the branch decision. Cost: a taken transfer that is the first fetch after a
  redirect is not predicted.
- **The guess travels with the instruction** (a taken bit and the low target bits; the upper bits are
  the pc's) to EX, which redirects when the direction was wrong, the target was wrong (all bits are
  compared), or an instruction that is no transfer was predicted taken (a stale entry after FENCE.I,
  or an aliased tag). A wrongly predicted fall-through continues at `pc + 4`.
- **Partial tags and targets** (10 and 20 bits) instead of full ones: about 2000 flip-flops would be
  half of the core's, clocked in every cycle. Wrong guesses from aliasing cost cycles, never
  correctness, because EX checks every guess.
- **Updates** come from every control instruction that leaves EX and is not dropped; a call or a
  return is recognised by its link register (x1 or x5), as the RISC-V specification suggests for return
  address stacks.

## Measurements

2026-11-26, release build, BRAM, `MUL_BITS = 4`, RTL of `3c7be96` plus Sol 2 (not yet committed).
Cycles: `make bench` (29 programs); energy: `make energy` (ADR 016); delay: `scripts/area.py --xc7`
(Yosys 0.66, logic-only arrival time on xc7, no routing, not Fmax; one synthesis per configuration,
and ABC moves such numbers by several percent between runs).

| | `BP = 0` | BTB 8 | BTB 16 | BTB 32 |
|---|---|---|---|---|
| CoreMark/MHz | 1.961 | 2.135 | 2.175 | 2.198 |
| Geometric mean of speedups over Sol 1, 29 benchmarks | 1.215 | 1.329 | 1.344 | 1.354 |
| Cycles per CoreMark iteration | 510 631 | 468 344 | 459 892 | 455 052 |
| Energy per iteration (ADR 016 units) | 0.952 × 10¹⁰ | 1.033 × 10¹⁰ | 1.076 × 10¹⁰ | 1.173 × 10¹⁰ |
| Flip-flops | 3740 | 4345 | 4593 | 5089 |
| Energy × cycles | 4.86 × 10¹⁵ | 4.84 × 10¹⁵ | 4.95 × 10¹⁵ | 5.34 × 10¹⁵ |
| Logic delay of the core, xc7 | 6168 ps | 7314 ps | 6856 ps | not measured |
| Time per iteration (cycles × delay) | **3.150 ms** | 3.425 ms | 3.153 ms | |
| Energy × time (relative) | **3.00** | 3.54 | 3.39 | |
| Core: LUT6 (generic) | 6497 | 7151 | 7267 | |
| Core: transistors (CMOS estimate) | 145 648 | 159 594 | 163 772 | 172 122 |

The trace model of the design predicted the cycle counts well (2.18–2.21 CoreMark/MHz against the
measured 2.135–2.198). It could not predict the two costs that decide:

- **Energy per cycle grows by 18–38 %** (18.6 thousand units per cycle without prediction, 22.0, 23.4 and 25.8
  thousand with BTB 8, 16, 32). The tables add 605 to 1349 flip-flops (+16 to +36 %), all clocked
  every cycle, and their lookup and update logic toggles every cycle. The 8–11 % fewer cycles do not pay
  for it.
- **The logic path grows by 700–1100 ps.** The longest path with BTB 16 runs from the WB register
  through forwarding into the 64-bit target adder of EX, then through the compare of the actual
  with the predicted target, to the misprediction decision and the fetch queue (`fq_pop`,
  `fetch_accept`). In Sol 1 the redirect decision needs only the branch comparison; the target sum goes
  to the fetch address, not into the decision.

So with BTB 16 one iteration takes the same time as without prediction (3.153 against 3.150 ms) and
costs 13 % more energy. BTB 8 is worse in both. BTB 32 already loses on energy × cycles. (The energy
numbers are the corrected ones of ADR 016; the first version had +12 %, with the same conclusion.)

## Verification

- `tb/unit/test_bpred.py`: the unit alone (10 tests); `make mutate M=bpred`: 17 mutants, all caught.
- Every core test runs in the configurations `sol1` (`BP = 0`, `MUL_BITS = 1`), `sol2` (`BP = 0`,
  `MUL_BITS = 4`) and `bp` (`BP = 1`, `MUL_BITS = 4`); the exact-cycle tests of Sol 1 still hold in `sol1`. New tests with exact flush counts: a loop, calls and
  returns from one site, returns to several sites and through x5, a branch waiting in the full fetch
  queue. New correctness tests: a JALR whose target changes in the low bits, a JALR whose target moves
  by exactly 2^20 (the BTB keeps only 20 bits), a branch overwritten by FENCE.I while the BTB still
  predicts it, an exception on the predicted path, a misaligned jump, a branch dropped by FENCE.I.
- `make mutate M=core_pipe`: the first runs found four gaps in the tests (one of them a correctness
  gap: the low target bits were never compared in a test) and one redundant term (see below).
- Spike comparison (C programs and 24 random programs, also with a stalling memory), `riscv-tests`,
  `release-check`, and the 29 self-checking benchmarks.

**A trapping jump is learned.** The first version did not update the predictor from a jump that
traps on its misaligned target. Mutation testing showed this cannot be observed: the BTB drops target
bits 1:0, so a later guess fetches an aligned word, and the trap in WB drops that path anyway. The
term was removed.

## Consequences

- Sol 2 runs without prediction: a taken branch costs two cycles, as in Sol 1.
- With `BP = 1` a correct prediction costs nothing and a misprediction two cycles; `perf_flushes_o`
  counts redirects (mispredictions and WB redirects).
- The tests run in three configurations: `sol1`, `sol2` (the default) and `bp` (`BP = 1`).
- **What would make prediction pay off**, each to be measured the same way:
  - take the target check out of the EX path: branches and JAL have their target `pc + imm` in ID
    already, so ID can compare it with the guess and pass one bit to EX; only JALR needs the EX sum,
    and its guesses come from the return stack;
  - fewer clocked flip-flops: a smaller BHT, shorter tags, and clock gating of the tables (they change
    only on an update);
  - on a board, check that this path, not the memory or the multiplier, sets the clock.
