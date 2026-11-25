# ADR 002: One `alu` with a `word_i` input, and the `{funct7[5], funct3}` encoding

Status: draft, pending author review

## Context

RV64I has 5 W instructions without an immediate (ADDW, SUBW, SLLW, SRLW, SRAW) and 4 with one (ADDIW, SLLIW, SRLIW, SRAIW). They perform the same operation as the 64-bit ones, but on the low 32 bits, and sign-extend the result. RV32 does not have them, and the project rules require the XLEN = 32 configuration to build as well. We need to decide where the W variants live and how ALU operations are encoded.

## Options

| Option | Pros | Cons |
|---|---|---|
| **A. One `alu` with a `word_i` input** | One adder and one shifter for both modes; the interface does not depend on XLEN; the decoder needs one bit | The module gains logic before the input (shift operand) and after the output (sign extension); needs the contract "the decoder sets `word_i` only for five operations" |
| B. A separate `alu_w` module next to `alu` | Each module is simpler; W does not touch the 64-bit path | A second adder and shifter, an extra result mux in the datapath; more consistency tests |
| C. Separate operation codes for W (`ALU_ADDW`, …) | No extra input | 15 codes instead of 10, the code no longer fits `{funct7[5], funct3}`; every operation is described twice |

The area of the options was not measured. The claim about duplicated hardware in option B is reasoning and should be checked by synthesis once the datapath exists.

A separate choice was the encoding of `alu_op_t`: arbitrary values, or `{funct7[5], funct3}` taken from the instruction. The latter lets the decoder simply concatenate bits for R-type instead of using a table.

## Decision

1. Option A: one `alu` module with a `word_i` input. For XLEN = 64 the W logic is guarded by `if (riscv_pkg::XLEN == 64)`; for XLEN = 32 it is not built, and `word_i` stays in the port list and is tied off (`unused_word`).
2. The W logic has three parts: the shift amount (5 bits instead of 6), operand preparation for SRLW and SRAW (zeros or the sign of bit 31 instead of the upper 32 bits), and sign extension of the result from bit 31.
3. `alu_op_t` is encoded as `{funct7[5], funct3}`: ADD = `0000`, SLL = `0001`, SLT = `0010`, SLTU = `0011`, XOR = `0100`, SRL = `0101`, OR = `0110`, AND = `0111`, SUB = `1000`, SRA = `1101`.
4. Contract with the decoder: `word_i = 1` only for ADD, SUB, SLL, SRL, SRA. For OP-IMM, bit `inst[30]` is not used in `op_i`, except for shifts.

## Consequences

- For R-type the decoder builds `op_i` as `{inst[30], inst[14:12]}` without a table. For OP-IMM it must clear the top bit, except for SLLI, SRLI, SRAI; a missing clear would make ADDI execute as SUB. This needs a dedicated decoder test.
- For undefined combinations (`word_i = 1` with AND, OR, XOR, SLT, SLTU) the result is sign-extended, because the extension does not depend on `op_i`. This is not a bug, but the behaviour must not be relied on.
- For XLEN = 32, Verilator would warn about an unused `word_i`; the tie-off `&{1'b0, word_i}` in a signal with the `unused` prefix silences it.
- Three pitfalls found and covered by the tests: the W shift uses 5 bits (`SLLW(1, 32) = 1`), the SRAW sign comes from bit 31, not 63, and the result is sign-extended.
- If later synthesis shows that the result sign extension is on the ALU critical path, it can move out of the module (into the EX or MEM stage). That changes the interface, so it would be a separate ADR with before/after measurements.
