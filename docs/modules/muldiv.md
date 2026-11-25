# Multiply and divide (`rtl/core/muldiv.sv`)

Diagram: [img/muldiv.svg](../img/muldiv.svg). Decisions and measurements: [ADR 009](../adr/009-muldiv-iterative.md), [ADR 011](../adr/011-muldiv-short-path.md) (short path, `last_q`). Connections between modules and area: [overview.md](overview.md).

The iterative unit of the M extension: MUL, MULH, MULHSU, MULHU, DIV, DIVU, REM, REMU and (RV64) MULW, DIVW, DIVUW, REMW, REMUW. One state machine, one adder, `XLEN` iterations, or `XLEN / 2` on the short path.

## Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `clk_i`, `rst_ni` | input | 1 | Clock, synchronous reset of the state machine (data registers have no reset) |
| `valid_i` | input | 1 | An instruction is waiting for the result; hold 1 until `done_o` |
| `abort_i` | input | 1 | The instruction was flushed (an exception in WB, ADR 014): back to `IDLE` at the next edge, no `done_o` |
| `funct3_i` | input | 3 | 000 MUL, 001 MULH, 010 MULHSU, 011 MULHU, 100 DIV, 101 DIVU, 110 REM, 111 REMU |
| `word_i` | input | 1 | W instruction (RV64 only): operands from the low 32 bits, the result is sign-extended |
| `a_i`, `b_i` | input | `XLEN` | rs1 and rs2 |
| `done_o` | output | 1 | 1 for one cycle when `result_o` is valid |
| `result_o` | output | `XLEN` | Result (valid only when `done_o = 1`) |

## Protocol and latency

1. First cycle (state `IDLE`, `valid_i = 1`): the unit copies the operands into its own registers.
2. `N` cycles in state `RUN`, one iteration per cycle: `N = XLEN / 2` on the short path, otherwise `N = XLEN`.
3. State `DONE`: `done_o = 1`, `result_o` is valid. The core commits the instruction in this cycle. The unit then returns to `IDLE` by itself, so the next M instruction can raise `valid_i` in the following cycle.

From `valid_i` to `done_o` there are `N + 1` clock edges. An instruction takes `N + 2` cycles including the commit, of which `N + 1` are stall cycles: 34 or 66 cycles for XLEN = 64, 18 or 34 for XLEN = 32. The operands may change after the first edge: the unit works on its own copies (a test checks this).

**Short path** (`N = XLEN / 2`, ADR 011):
- MUL and MULW when the upper half of the multiplier `b` is zero (MULW always);
- DIV, DIVU, REM, REMU and the W forms when the dividend magnitude is below 2^(XLEN/2) and the divisor is not 0.

MULH, MULHSU, MULHU and division by zero always take the full path. The latency depends on the operand values.

## How it computes

**Multiplication.** The pair `{hi, lo}`; initially `hi = 0`, `lo` = the multiplier, and the multiplicand sits in `opa`, extended to `XLEN + 1` bits (with its sign if it is signed). Each step: `hi += opa` if `lo[0] = 1`, then the pair shifts right by one bit (arithmetically if the multiplicand is signed). In the last step a signed multiplier is subtracted instead of added: its top bit has a negative weight. After `XLEN` steps the product is `{hi, lo}`; MUL returns `lo`, MULH, MULHSU, MULHU return `hi`.

**Division.** Restoring division of the magnitudes. `hi` holds the partial remainder, `lo` first the dividend and then the quotient. Each step: try `hi - divisor`; if there is no borrow (`ge`), the remainder becomes the difference and a 1 enters the quotient, otherwise the remainder stays. Then the pair shifts left and the next dividend bit enters `hi`. After the last step `hi` has been shifted once more, so the remainder is `hi[XLEN:1]` and the quotient is `lo`. Result sign: the quotient is negative when the signs differ (but not on division by zero); the remainder takes the sign of the dividend.

**Special cases** (they arise by themselves, there is no dedicated logic):

| Case | DIV, DIVU | REM, REMU |
|---|---|---|
| division by 0 | all ones | the dividend |
| `MIN / -1` | `MIN` | 0 |

**W instructions.** The low 32 bits are sign-extended (signed operations) or zero-extended; the result is sign-extended from bit 31. DIVUW by zero gives all ones (2^32 − 1 after sign extension).

**Short path.** For MUL, after `XLEN / 2` steps the low half of the product is `{hi[XLEN/2−1:0], lo[XLEN−1:XLEN/2]}`. For division the dividend is loaded shifted up by `XLEN / 2`, and the quotient and remainder end up where they do on the full path. The "fits" condition is read from the operand bits (without the 64-bit negation). The last-step flag `last_q` is a register, not a counter compare in front of the adder.

**The adder** is shared by both operations: `hi ± y` in `XLEN + 1` bits, where `y = opa` always for division, and for multiplication only when `lo[0] = 1`. In division `sum[XLEN]` is the borrow (the reasoning is in ADR 009).

## Testing

`tb/unit/test_muldiv.py`, `make unit T=muldiv`, XLEN = 64 and 32. The models in `tb/unit/mext.py` are written from the specification. Tests: known results (including `MIN / -1`, division by 0, `MULH(-1, -1)`, `MULHSU`, W cases); all pairs of edge operands for every operation; random operands with varied divisor lengths; the latency cycle by cycle; the protocol; independence from input changes after loading.

After ADR 011: the latency model `mext.iterations()` (every operation checks both the result and the exact number of cycles), short-path boundaries from both sides, random operand lengths, garbage in every register without reset before the first operation. Mutation check: `make mutate M=muldiv`, 34 mutants, all caught.

In the core: `tb/unit/test_core.py` (all 13 M instructions, `rd = x0`, the same register as source and destination, a loop with `MUL`, stall counters from the latency model) and the Spike comparison on `sw/muldiv.c` (edge values) and `sw/mbench.c` (ordinary numbers).

## Area

Measured 2026-11-25, Yosys 0.66, `make area`, no commit hash (estimate): XLEN = 64: 954 LUT6 + 211 flip-flops; on xc7 762 LUTs + 67 CARRY4, logic delay 3885 ps without routing. XLEN = 32: 447 LUT6 + 113 flip-flops; on xc7 342 LUTs, 2973 ps. Before ADR 011: 809 LUT6 and 4311 ps (XLEN = 64). Fmax not measured.

## Status

`make lint` and `make struct` are clean, `make unit` passes, Yosys synthesizes the module. Not done: radix-4, multiplication on DSPs (board-dependent), a registered result (for frequency), a constant-time mode (disables the short path).
