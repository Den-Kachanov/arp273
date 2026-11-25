# ADR 010: ALU — comparison on an extended adder, direct decoding, flat result mux

Status: draft, pending author review

## Context

The author asked for an assessment of the ALU and an optimisation of area and speed. The version from ADR 007 already shares one adder and one right shifter between operations. The questions: what else can be removed without losing speed, and what does a speed-up cost.

Metrics (all from one run, Yosys 0.66):
- **area:** `synth; abc -lut 6` (as in the earlier ADRs) and `synth_xilinx -family xc7 -abc9` (Xilinx 7 series, the Arty A7 candidate; counts LUTs and CARRY4 separately);
- **speed:** the latest arrival time from `sta` with the cell delays of the Yosys xc7 library. This is **logic only, without routing**, which on an FPGA often adds as much again. The number is good for comparing options, not as Fmax. Only place-and-route on the board gives Fmax.

Critical path of the current ALU on xc7 (XLEN = 64): LUT (`b ^ sub`) → 64-bit carry chain (16 CARRY4, ≈ 1.8 ns) → two LUTs of the result mux and the W extension.

## Options

| Option | Change |
|---|---|
| v0 | ADR 007 (current) |
| **v1** | (1) SLT and SLTU from bit X of an X+1-bit adder: the operands are extended by the sign for SLT and by zero for SLTU, so `a − b` cannot overflow and bit X equals `a < b`; the separate `lt`/`ltu` and the choice between them disappear; (2) the operation is selected straight from `funct3` (`funct7[5]` only tells SUB/SRA apart); (3) the output is an AND-OR of one-hot selects instead of a `case`; (4) `fill` for SRA without a `word_i` mux (see below) |
| v2 | v1 + output "adder or everything else" as one LUT after the carry chain |
| v3 | v1 + carry-select: the upper half of the adder is computed twice (carry-in 0 and 1) in parallel with the lower half |
| v4 | v2 + carry-select |

## Measurements

ALU alone, measured 2026-11-25, no commit hash (estimate). Format: generic LUT6 / xc7 LUT / xc7 CARRY4 / xc7 logic delay.

| | XLEN = 64 | XLEN = 32 |
|---|---|---|
| v0 | 763 / 747 / 17 / 3634 ps | 323 / 285 / 8 / 2590 ps |
| **v1 (adopted)** | **700 / 603 / 17 / 3539 ps** | **293 / 276 / 9 / 2481 ps** |
| v2 | 736 / 643 / 17 / 3464 ps | 293 / 273 / 9 / 2597 ps |
| v3 | 690 / 652 / 27 / 3027 ps | 297 / 284 / 15 / 2268 ps |
| v4 | 738 / 674 / 27 / 2959 ps | 302 / 283 / 15 / 2297 ps |

v1–v4 were measured before the `fill` simplification; for v1 the table shows the figure after it (the code in the repository). ABC varies by 1–2% in LUTs and up to ~100 ps between structurally similar versions: v1 before and after renaming signals gave 705/614 and 703/611, so differences of that size are not treated as results.

Whole core (flat synthesis, same run, `scripts/area.py`):

| | generic LUT6, X = 64 | generic LUT6, X = 32 | xc7 LUT, X = 64 | xc7 LUT, X = 32 |
|---|---|---|---|---|
| with v0 | 4377 | 2211 | 2295 | 1042 |
| with v1 | 4419 | 2186 | 2241 | 1051 |

The first carry-select attempt (v3) was built by Yosys as `hi0 + 1` after `hi0`, that is, in series, with no gain (3358 ps). The carry-in has to enter the same adder: `{a, 1} + {b, 1}`, then drop the low bit. The numbers in the table are for the correct version.

## Verification

- **Equivalence:** a SAT proof in Yosys (`sat -prove`) for XLEN = 64 and 32, v1 and v3 against v0, and separately for the code in the repository: for all inputs within the decoder contract the result is identical.
- **Decoder contract:** without a constraint the proof fails on `op_i = 1110`: the decoder never produces that code, v0 returns 0 there, v1 computes OR. The proof therefore covers the 10 legal codes, with `word_i = 1` only together with ADD, SUB, SLL, SRL, SRA.
- **Tests:** `make unit` 18 passed; `make regress` (4 programs) and `make fuzz` (40 seeds) match Spike; `make lint` clean.
- **Mutations:** `scripts/mutate.py alu`, 22 mutants, all caught.

**The `fill` simplification.** The mutant "SRAW takes the fill sign from bit 63 instead of 31" was expected to survive, and that is correct, not a gap in the tests. For SRAW the shift amount is at most 31, so the fill bit never reaches the low word, and the upper word is replaced by the sign extension anyway. The `word_i` mux for `fill` was removed, and the equivalence was proved.

## Decision

**v1.** For the ALU alone it is no worse than v0 on any metric (−8% generic LUT6, −19% LUTs on xc7, delay within noise), and it is easier to read: the comparison is now one formula and the decoder contract is written in the code.

**Carry-select (v3) is not enabled.** It gives −14% ALU logic delay at the cost of about +40–50 LUTs and +10 CARRY4 (XLEN = 64), and almost nothing for XLEN = 32 in this measurement. It is a speed feature, and by the project rules it is only enabled after measurements. It is not yet known whether the ALU will be on the critical path: in `make area --xc7`, `muldiv` (register to register) already has 4311 ps against 3539 ps in the ALU, and only place-and-route will show the real critical path of the pipeline. The option is described above so that it can be revisited in a new ADR.

Follow-up measurement of carry-select inside the whole core (same run conditions, xc7): XLEN = 64: 2241 → 2290 LUT (+2.2%), +10 CARRY4, logic delay 7134 → 6902 ps (−3.3%); XLEN = 32: LUTs unchanged, +6 CARRY4, 5730 → 5774 ps (noise). The CPI does not change, so the gain is at most a few percent of frequency, and zero while `muldiv` has the longer path.

## Consequences

- **The gain inside the core is small:** −54 LUTs on xc7 (−2.4%) for XLEN = 64; for XLEN = 32 and for generic LUT6 it is within noise. Flat synthesis of the core already found part of this by itself. The main benefit is a smaller and simpler module, not a noticeably smaller core.
- **Undefined codes:** for codes outside `alu_op_t` the result is now undefined (previously 0). The decoder never produces them; this is stated in `alu.sv` and `docs/modules/alu.md`.
- **ALU speed** is limited by the 64-bit carry chain. Without carry-select or a different adder it cannot be reduced further.
- **ADR 007** remains valid for the shared adder and shifter; ADR 010 changes only the comparison, the decoding and the result mux.
- **Reproduction:** `make area` (`scripts/area.py --xc7`) gives the tables for the ALU in the repository and for the core; the v2–v4 table is reproduced from the description above, their code is not kept in the repository.
