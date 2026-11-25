# ADR 009: M extension: one iterative multiply/divide unit

Update: The latency was changed in [ADR 011](011-muldiv-short-path.md): a short path of XLEN/2 iterations and the `last_q` register

## Context

The M extension adds 8 instructions (MUL, MULH, MULHSU, MULHU, DIV, DIVU, REM, REMU) and 5 W variants for RV64 (MULW, DIVW, DIVUW, REMW, REMUW). The plan places M in stage 2, but it was added now so that ordinary C code (multiplication, division, `%`) builds without `libgcc`. The project rules require division to be multi-cycle. The FPGA board is not chosen yet, so it is unknown whether DSP blocks are available. The single-cycle core executes one instruction per cycle, so a multi-cycle operation needs a stall.

## Options

| Option | Pros | Cons |
|---|---|---|
| A. Combinational `a * b` for multiplication, multi-cycle division only | Multiplication in one cycle | A separate 64 × 64 multiplier takes 9117 LUT6 in generic Yosys synthesis (no DSP); it lengthens the critical path of the single-cycle core; on an FPGA the gain depends on DSPs, and the board is not chosen |
| **B. One iterative unit: shift-and-add multiplication, restoring division, one adder for both** | Small (809 LUT6, 209 flip-flops); one stall protocol for all 13 instructions; independent of DSPs | `XLEN + 1` cycles for every M instruction |
| C. Radix-4 (Booth) and/or early termination | 2× fewer cycles, or a variable latency | More area and complexity; the gain must be measured first |
| D. Separate multiply and divide units | Both can work at once | In an in-order core the second unit sits idle |

## Decision

Option B, the module `rtl/core/muldiv.sv` ([description](../modules/muldiv.md), diagram [img/muldiv.svg](../img/muldiv.svg)).

- **Protocol.** The core holds `valid_i`, `funct3_i`, `word_i`, `a_i`, `b_i` stable until `done_o`. The first cycle loads the operands (the unit keeps its own copies), then `XLEN` iteration cycles follow, then `done_o = 1` for one cycle. In that cycle the core commits the instruction and moves on. From `valid_i` to `done_o` there are `XLEN + 1` clock edges, so an instruction takes `XLEN + 2` cycles including the commit (66 for XLEN = 64, 34 for XLEN = 32).
- **Stall in the core.** `stall = muldiv & ~done`. While `stall = 1`, the PC does not move, no register is written, `commit_valid_o = 0`, and the `stalls` counter increments. This is the first stall in the project, and the counter becomes meaningful for the first time.
- **Multiplication.** The pair `{hi, lo}`: `lo` starts as the multiplier; on every step `hi += multiplicand` (if `lo[0] = 1`) and the pair shifts right. A signed multiplicand is extended to `XLEN + 1` bits, a signed multiplier is subtracted in the last step (its top bit has a negative weight). One circuit thus does MUL, MULH, MULHSU, MULHU.
- **Division.** Restoring division of the magnitudes `|a|` and `|b|`; the signs are fixed at the end. The special cases are not coded explicitly but follow from the circuit:
  - division by zero: the quotient is all ones and the remainder equals the dividend (a zero divisor always allows the subtraction); the quotient is not negated;
  - overflow `MIN / -1`: the magnitude `2^(XLEN-1)` fits in an unsigned number, the quotient comes out as `MIN`, the remainder as 0.
- **W instructions.** The operands are the low 32 bits, sign- or zero-extended depending on the signedness of the operation; the result is sign-extended from bit 31.
- **One adder.** `hi ± y` in `XLEN + 1` bits for both operations. In division both `hi` and the divisor are below `2^XLEN` (the dividend has `XLEN` bits, so the partial remainder after `j` steps is below `2^j`), hence the difference lies in `(-2^XLEN, 2^XLEN)` and `sum[XLEN]` is exactly the borrow. The adder was first two bits wider with sign extension; the mutation check showed that those bits did not affect the result, and they were removed.

## Measurements

All figures from Yosys 0.66, 2026-11-24, `synth -top <module>` and `abc -lut 6`, no commit hash (estimate; ABC is heuristic and numbers move by a few percent between runs, so only figures from the same run are compared).

| | XLEN = 64 | XLEN = 32 |
|---|---|---|
| `muldiv` | 809 LUT6 + 209 flip-flops | 382 LUT6 + 111 flip-flops |
| `decoder` | 47 LUT6 (was 40) | 39 LUT6 (was 37) |
| whole core (flat synthesis) | 4368 LUT6 + 2513 flip-flops | 2237 LUT6 + 1391 flip-flops |
| the same core with a stub instead of `muldiv` | 3458 LUT6 + 2240 flip-flops | 1771 LUT6 + 1216 flip-flops |
| for comparison: combinational `a * b` 64 × 64 | 9117 LUT6 | |

- The difference between the core with and without `muldiv`: +910 LUT6 and +273 flip-flops for XLEN = 64. `muldiv` itself is 809 LUT6 and 209 flip-flops; the rest is the `stalls` counter (64 flip-flops and an adder: without M it is always 0 and Yosys removed it), decoding and the `wb_sel` mux.
- The flip-flop count of the XLEN = 64 core adds up: 1984 (`regfile`) + 64 (PC) + 256 (counters) + 209 (`muldiv`) = 2513.
- Performance. `sw/muldiv.c` (3848 M instructions out of 11514, a deliberate worst case): 261634 cycles, CPI 22.723, `stalls` = 250120 = 3848 × 65. Programs without M (`fib`, `mem`, `alu`) did not change, CPI 1.000. This is a synthetic program, not an estimate for real programs.
- Fmax was not measured (needs place-and-route and a board).

## Verification

- Unit test `tb/unit/test_muldiv.py` (XLEN = 64 and 32): known results from the specification, written by hand; all pairs of edge operands for the 8 operations (and for the 5 W ones); random operands with small divisors; latency checked cycle by cycle; the protocol (`done_o` for one cycle, nothing without `valid_i`, back-to-back operations); the unit does not depend on input changes after loading. The reference model `tb/unit/mext.py` is written from the specification on Python integers, not from the structure of the RTL.
- Mutation check: 15 broken copies of `muldiv.sv`. 12 were caught, 3 survived, and all three turned out to be equivalent (adder bits that do not affect the result), which led to the simpler adder. After the simplification, 8 new mutants (borrow, shift sign, remainder, multiplicand extension, quotient bit and others) were all caught. Decoder: 6 mutants, 5 caught, 1 equivalent (the defensive clearing of `muldiv_o` for an illegal instruction, which is unreachable anyway). Integration into the core: 8 mutants (PC during the stall, register write during the stall, commit during the stall, the stall counter, a missing `WB_MD`, stall without `done`, swapped operands, a lost `word`), all caught.
- Spike comparison (`--isa=rv64im`): `muldiv` 11514 instructions, `alu` 520, `fib` 274, `mem` 450, all match.

## Consequences

- Every M instruction costs `XLEN + 2` cycles. For multiplication-heavy programs this matters (CPI 22.7 in the worst case); it is the known price of the small area.
- Optimisation options, each enabled only after before/after measurements recorded in an ADR: early termination (skipping leading zeros), radix-4, 32 instead of 64 iterations for W instructions, removing the load cycle, a separate DSP multiplier once the board is known. None of these is measured yet. (Update: ADR 011 adds a short path of XLEN/2 iterations.)
- Pipeline (week 2): the unit sits in EX and the earlier stages wait. The protocol (hold `valid_i` until `done_o`; the unit copies the operands) fits this. There is no abort input: the pipeline must not start an M instruction speculatively, or such an input has to be added.
- The unit has no reset on its data registers (only on the state machine), like `regfile` (ADR 005): values are written before they are read.
- Programs are now built with `-march=rv64im_zicsr`, Spike runs with `--isa=rv64im`; `libgcc` is not needed for multiplication and division.
