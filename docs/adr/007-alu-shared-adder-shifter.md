# ADR 007: ALU with one adder and one right shifter

Status: draft, pending author review. The comparison, the decoding and the result mux were changed in [ADR 010](010-alu-flat-select-shared-compare.md); the shared adder and shifter remain

## Context

The first version of `alu.sv` described every operation as a separate expression: an adder, a subtractor, two comparators and three shifters. Synthesis showed that the three shifters took about 60% of the module's LUT6s. Keep the original version, or share hardware between operations?

## Options

| Option | Pros | Cons |
|---|---|---|
| A. A separate expression for every operation | Reads like the specification; a mistake is visible at once | Larger area |
| **B. One adder for ADD, SUB, SLT, SLTU and one right shifter for SLL (via bit reversal), SRL, SRA** | Smaller area | Harder to read; SLL through reversal is not obvious |

## Decision

Option B. The original version stays in `alu.sv` as the `ORIGINAL` comment.

## Measurements

Yosys 0.66, `synth -top alu`, `abc -lut 6`, 2026-11-23. No commit hash: the repository had no git at the time, so this is an estimate, not an official measurement.

| | A, XLEN = 64 | B, XLEN = 64 | A, XLEN = 32 | B, XLEN = 32 |
|---|---|---|---|---|
| Gates | 2682 | 1814 | 1235 | 851 |
| LUT6 | 983 | 763 | 380 | 323 |
| Logic depth (gate levels) | 130 | 128 | 65 | 69 |

Option B was compared with A by a SAT proof in Yosys (`miter`, `sat -prove-asserts`) for XLEN = 64 and 32: the result is identical for all inputs. The cocotb tests pass for both XLEN values.

## Consequences

- About 22% fewer LUT6 (XLEN = 64), logic depth almost unchanged; Fmax was not measured, because only place-and-route on a specific board gives it.
- For XLEN = 32 the depth grew slightly (65 → 69); whether this matters will be seen in the datapath.
- If the ALU ends up on the core's critical path, option A can be restored from the comment. That is a new ADR with before/after measurements.
