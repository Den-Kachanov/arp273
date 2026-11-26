# ADR 011: `muldiv` — a short path of XLEN/2 iterations and a last-step register

Update: with several multiplier bits per cycle ([ADR 017](017-multiplier-bits-per-cycle.md)) the short path takes XLEN/2 / `MUL_BITS` iterations for a multiplication.

## Context

`muldiv` (ADR 009) computes every M instruction in XLEN iterations: 66 cycles per instruction for XLEN = 64. On a program with ordinary numbers (`sw/mbench.c`: an `int` matrix product, hashing, decimal conversion, GCD, division of small `int`s), 91% of the single-cycle core's cycles were spent waiting for M. The author asked to speed the unit up: fewer cycles and a shorter critical path, keeping in mind that the design is later to be taken to silicon.

Critical path of `muldiv` on xc7 (`sta`, logic only): counter register → compare `cnt_q == 0` (`last`) → operand select → 65-bit carry chain → mux → register, 4311 ps.

## Options

| Option | Cycles | Area | Note |
|---|---|---|---|
| A. Early termination of multiplication (stop when the rest of the multiplier is zero) | large gain for small multipliers | a 128-bit shifter to move the product by the remaining positions: hundreds of LUTs | it was wrongly described to the author as cheap before this analysis |
| **B. Short path of XLEN/2 iterations** for operands that fit in half a word | half for such operands | dividend load mux, one more result-select input, zero detectors | the result is taken from other wires, no shifter |
| C. Radix-4 (2 bits per cycle) | half for all operands | a second adder or Booth recoding, about +50% of the unit | deferred until CoreMark measurements |
| D. FPGA DSP blocks for multiplication | a few cycles | DSPs, board-dependent | does not carry over to silicon; deferred until the board is chosen |
| **E. A `last_q` register** instead of comparing the counter in front of the adder | unchanged | +1 flip-flop | frequency only |

## Decision

**B + E.**

**Short path (B).** XLEN/2 iterations when:
- **MUL** (and MULW): the upper half of the multiplier `b` is zero. After XLEN/2 steps the product is `hi · 2^(XLEN/2) + lo[XLEN−1:XLEN/2]`, so the low half of the product is `{hi[XLEN/2−1:0], lo[XLEN−1:XLEN/2]}`, just other wires. MULW always takes this path: MUL operands are unsigned, and W uses the low 32 bits.
- **DIV, DIVU, REM, REMU** (and the W forms): the dividend magnitude is below 2^(XLEN/2) and the divisor is not zero. The dividend is loaded shifted up by XLEN/2, so the steps that would only shift zeros are skipped. The quotient ends up in `lo` as usual: the bits above it are the zeros loaded below the dividend. The remainder is `hi[XLEN:1]`, as before.
- **Always the full path:** MULH, MULHSU, MULHU (the upper half is needed) and division by zero (on the short path the all-ones 64-bit quotient would only be 32 bits wide).

**The condition "dividend magnitude below 2^(XLEN/2)" is read from the operand bits**, not from `mag_a`, so it does not wait for the 64-bit negation:
- non-negative: the upper half is zero;
- negative: the upper half is all ones and the lower half is not zero, because −2^(XLEN/2) has a magnitude of exactly 2^(XLEN/2).

**`last_q` (E).** The last-iteration flag becomes a register: it is set when `cnt_q == 1` and cleared on load. The counter compare is no longer in front of the adder.

Clearing `last_q` on load is only needed for the first operation after power-up, when a flip-flop without reset may start at 1: otherwise the operation would end after one step. After every operation `last_q` returns to 0 anyway. The mutant without this clear survived at first, because Verilator initialised `last_q` to zero. Since then the tests start from random register values (ADR 012), and `test_garbage_after_power_up` writes garbage into every register without reset.

## Measurements

Measured 2026-11-25, Yosys 0.66, no commit hash (estimate). Area and cycles "before" were measured on a copy of the repository with the old `muldiv.sv` and the same `crt0`, "after" on the current one. `make area` and `make sim PROG=...`.

**Cycles** (single-cycle core, performance counters):

| Program | before | after | change |
|---|---|---|---|
| `mbench` (ordinary numbers) | 37976 (CPI 10.575) | 21560 (CPI 6.004) | **−43.2%** |
| `muldiv` (deliberate edge values) | 261665 (CPI 22.665) | 197441 (CPI 17.102) | −24.5% |
| `alu`, `fib`, `mem` (no M) | unchanged | unchanged | 0 |

**`muldiv` alone:**

| | before | after | change |
|---|---|---|---|
| LUT6 / flip-flops, XLEN = 64 | 809 / 209 | 954 / 211 | +17.9% LUT6 |
| LUT6 / flip-flops, XLEN = 32 | 381 / 111 | 447 / 113 | +17.3% LUT6 |
| xc7 LUT / CARRY4, XLEN = 64 | 559 / 67 | 762 / 67 | +36.3% LUT |
| xc7 logic delay, XLEN = 64 | 4311 ps | 3885 ps | **−9.9%** |
| xc7 logic delay, XLEN = 32 | 3334 ps | 2973 ps | −10.8% |
| CMOS transistors (`abc -g cmos2`, `stat -tech cmos`), XLEN = 64 | 17856 | 19642 | +10.0% |
| logic depth in gates (`ltp`), XLEN = 64 | 140 | 139 | — |

**Whole core:**

| | before | after | change |
|---|---|---|---|
| LUT6, XLEN = 64 / 32 | 4419 / 2186 | 4546 / 2317 | +2.9% / +6.0% |
| xc7 LUT, XLEN = 64 / 32 | 2241 / 1051 | 2371 / 1065 | +5.8% / +1.3% |
| xc7 logic delay, XLEN = 64 / 32 | 7134 / 5730 ps | 7069 / 6082 ps | see below |

The logic delay here excludes routing: it is for comparison, not Fmax. The longest path of the single-cycle core for XLEN = 32 does not go through `muldiv`: `imem` → decoder → ALU adder → jump-target alignment → `trap` → `instret` counter. That logic is identical in both versions, so the change 5730 → 6082 ps comes from ABC laying out the neighbouring logic differently, not from the new steps. In the pipeline this path is cut by the stage registers.

## Verification

- **Model:** `mext.iterations()` states the rule of this ADR independently of the RTL. Every operation in `test_muldiv.py` checks both the result and the exact number of cycles to `done_o`.
- **New tests:**
  - `test_short_path_boundaries`: values on both sides of every boundary: `b = 2^h − 1` and `2^h`, `±(2^h − 1)` and `−2^h`, division by zero, MIN / −1, W;
  - `test_random_magnitudes`: 150 pairs of random lengths, 8 operations and 5 W;
  - `test_garbage_after_power_up`;
  - `test_protocol` cycle by cycle for a short and a full operation.
- **Core test:** the expected stalls come from the same model for every M instruction.
- **Testing the tests:** the new tests fail on the latency when run against the old `muldiv`.
- **Mutations:** `make mutate M=muldiv`, 34 mutants (14 new ones for the short path and `last_q`), all caught; `core` 23 of 23.
- **Spike:** `make regress` (5 programs, including the new `mbench`) and `make fuzz` (40 seeds) match Spike, also with different seeds for the initial register values (`X_SEED` = 1, 2, 3, 7).
- **Other:** `make lint`, `make struct` (ADR 012) clean.

## Consequences

- **The latency depends on the data:** XLEN/2 + 1 or XLEN + 1 cycles from `valid_i` to `done_o`. The protocol did not change (the core waits for `done_o`), but the execution time now depends on operand values. For cryptographic code this is a timing side channel; if constant time is ever needed, an input that disables the short path is required.
- **Area:** `muldiv` grows by 18% LUT6 (36% on xc7), the core by 3–6%. The cycle gain depends on the share of M instructions. On the single-cycle core it is large, because everything else takes 1 cycle. On the pipeline and on CoreMark the share will be smaller, and the measurement has to be repeated there.
- **Longest module path:** `muldiv` (3885 ps on xc7) is still longer than the ALU (3539 ps). The next frequency steps: register the result (+1 cycle) or split the 65-bit adder.
- **Portability:** no FPGA primitives, synchronous reset, no latches; it carries over to silicon as is.
