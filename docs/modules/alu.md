# ALU (`rtl/core/alu.sv`)

Status: draft, pending author review

The combinational ALU of the RV64I core. It performs 10 operations on XLEN-bit operands; five of them have W variants (computed in 32 bits and sign-extended), selected by the `word_i` input and present only for XLEN = 64.

Design decisions: [ADR 002](../adr/002-alu-word-variants.md), [ADR 007](../adr/007-alu-shared-adder-shifter.md), [ADR 010](../adr/010-alu-flat-select-shared-compare.md). Diagram: [img/alu.svg](../img/alu.svg).

## Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `a_i` | input | `XLEN` | First operand |
| `b_i` | input | `XLEN` | Second operand; for shifts only the shift amount is taken from it |
| `op_i` | input | 4 (`riscv_pkg::alu_op_t`) | Operation code |
| `word_i` | input | 1 | 1: W variant (ADDW, SUBW, SLLW, SRLW, SRAW). The port exists for XLEN = 32 too, but is unused there |
| `r_o` | output | `XLEN` | Result |

The module is purely combinational: no clock and no reset. The output depends only on the current inputs.

## Operation encoding (`alu_op_t`)

The code equals `{funct7[5], funct3}` of an R-type instruction. For the decoder this is four wires, not a table.

| Operation | `op_i` | Result |
|---|---|---|
| `ALU_ADD` | `0000` | `a + b` |
| `ALU_SLL` | `0001` | `a << shamt` |
| `ALU_SLT` | `0010` | 1 if `a < b` as signed, else 0 |
| `ALU_SLTU` | `0011` | 1 if `a < b` as unsigned, else 0 |
| `ALU_XOR` | `0100` | `a ^ b` |
| `ALU_SRL` | `0101` | `a >> shamt`, zeros shifted in |
| `ALU_OR` | `0110` | `a \| b` |
| `ALU_AND` | `0111` | `a & b` |
| `ALU_SUB` | `1000` | `a - b` |
| `ALU_SRA` | `1101` | `a >>> shamt`, copies of the sign bit shifted in |

The decoder never produces other values of `op_i`, and their result is undefined (see the contract below). All additions and subtractions are modulo 2^XLEN; there is no overflow flag.

`shamt` = `b_i[5:0]` for XLEN = 64 and `b_i[4:0]` for XLEN = 32 (the low `$clog2(XLEN)` bits). The other bits of `b_i` are ignored.

## W variants (XLEN = 64 only)

With `word_i = 1` the operations ADD, SUB, SLL, SRL, SRA become ADDW, SUBW, SLLW, SRLW, SRAW:

- the operands are read as their low 32 bits;
- the shift amount is `b_i[4:0]` (5 bits);
- for SRLW the operand `a` is padded with zeros on the left, for SRAW with the sign of bit 31 (not bit 63);
- the result: bit 31 is copied into bits 63..32.

Examples: `ADDW(0x7FFFFFFF, 1) = 0xFFFFFFFF80000000`, `SLLW(1, 32) = 1`, `SRAW(0x80000000, 1) = 0xFFFFFFFFC0000000`.

## Contract with the decoder

- The decoder sets `word_i = 1` **only** for ADD, SUB, SLL, SRL, SRA (opcodes OP-32 and OP-IMM-32). AND, OR, XOR, SLT, SLTU have no W variants in RV64I, and their result with `word_i = 1` is not defined by the specification. In this module it is sign-extended as well, because the extension does not depend on `op_i`.
- For OP-IMM (ADDI, ANDI and so on), bit `inst[30]` belongs to the immediate, not to `funct7`. The top bit of `op_i` must be cleared for non-shift immediate operations, otherwise an ADDI with a negative immediate decodes as SUB. For SLLI/SRLI/SRAI, `inst[30]` is taken as in R-type.
- `op_i` is always one of the 10 codes in the table above. For other codes (for example `1110`) the ALU guarantees nothing: this keeps the result mux smaller (ADR 010).
- For XLEN = 32 `word_i` is ignored, but the port stays so that the interface does not depend on XLEN.

## Implementation

The diagram `img/alu.svg` shows the actual hardware structure.

- **Decoding.** `funct3` (`op_i[2:0]`) selects the operation, and `funct7[5]` (`op_i[3]`) only tells SUB from ADD and SRA from SRL. The select signals are one-hot: `is_add`, `is_sll`, `is_cmp` and so on.
- **An XLEN + 1-bit adder** for ADD, SUB, SLT, SLTU (subtraction as `a + ~b + 1`). For comparisons the operands are extended by one bit: with the sign for SLT, with zero for SLTU. Then `a − b` cannot overflow, and bit XLEN of the sum answers `a < b` in both cases. There is no separate comparator and no choice between a signed and an unsigned result.
- **One right shifter** for SLL, SRL, SRA. SLL reverses the input bits, shifts right and reverses the output; the output reversal is only wiring. SRA fills from the left with `a_i[XLEN−1]`. This is also correct for SRAW, because the shift amount is at most 31: the fill never reaches the low word, and the upper word is replaced by the sign extension. For SRLW and SRAW the upper word of the input is replaced with zeros or `a_i[31]`.
- **Output:** an AND-OR of the one-hot selects (every bit is a flat function of its own inputs); the comparison result is ORed into bit 0. For W operations bit 31 is then copied into 63..32.

The original version, with one expression per operation, stays in `alu.sv` as the `ORIGINAL` comment, as a readable specification.

Measurements (2026-11-25, Yosys 0.66, estimate, no commit hash; `make area`): XLEN = 64: 700 LUT6 (`abc -lut 6`); on xc7 603 LUTs + 17 CARRY4, logic delay 3539 ps without routing. XLEN = 32: 293 LUT6; on xc7 276 LUTs + 9 CARRY4, 2481 ps. Critical path: LUT → 64-bit carry chain → result mux. Comparison of the alternatives, including carry-select, in [ADR 010](../adr/010-alu-flat-select-shared-compare.md).

## Testing

File `tb/unit/test_alu.py`, run with `make unit T=alu`, both XLEN values (64 and 32). Compared against Python models for:

- edge values (0, 1, −1, largest positive, most negative);
- random pairs with a fixed seed;
- every shift amount from 0 to 2·XLEN + 1, including set upper bits of `b_i`;
- W variants: the same groups plus known results from the specification.

Testing the tests: `make mutate M=alu` (`scripts/mutants/alu.py`), 22 mutants, all caught. Equivalence of the new version with the old one was proved with SAT in Yosys for both XLEN values within the decoder contract (ADR 010).

## Status

- `verilator --lint-only -Wall` is clean for XLEN = 64 and 32 (`make lint`).
- Yosys 0.66 synthesizes the module (`make synth`); area and logic delay above, Fmax not measured (needs place-and-route and a board).
- No separate Spike comparison is needed for the ALU: it is covered at the core level.
