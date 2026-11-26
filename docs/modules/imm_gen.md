# Immediate generator (`rtl/common/imm_gen.sv`)

Diagram: [img/imm_gen.svg](../img/imm_gen.svg). Connections between modules and area: [overview.md](overview.md).

A combinational module: it assembles the immediate of the selected format from a 32-bit instruction and sign-extends it from bit 31 to `XLEN` bits.

Why the decoder selects the format: [ADR 006](../adr/006-imm-sel-from-decoder.md).

## Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `inst_i` | input | 32 | Instruction |
| `sel_i` | input | 3 (`riscv_pkg::imm_sel_t`) | Immediate format |
| `imm_o` | output | `XLEN` | Sign-extended immediate |

## Formats

| `sel_i` | Code | Immediate bits | Width |
|---|---|---|---|
| `IMM_I` | `000` | `inst[31:20]` | 12 |
| `IMM_S` | `001` | `{inst[31:25], inst[11:7]}` | 12 |
| `IMM_B` | `010` | `{inst[31], inst[7], inst[30:25], inst[11:8], 1'b0}` | 13 |
| `IMM_U` | `011` | `{inst[31:12], 12'b0}` | 32 |
| `IMM_J` | `100` | `{inst[31], inst[19:12], inst[20], inst[30:21], 1'b0}` | 21 |

Other values of `sel_i` give `imm_o = 0`. For U-type in RV64 the upper 32 bits copy `inst[31]`: `LUI 0x80000` gives `0xFFFFFFFF80000000`. The low 7 bits of the instruction (the opcode) do not take part in the immediate.

## Testing

File `tb/unit/test_imm_gen.py`, run with `make unit T=imm_gen`, XLEN = 64 and 32. The Python models assemble the immediate from the bits according to the specification. Seven tests: edge instructions, random instructions (seed 1), sign extension, bit 0 is zero in B and J, known U values, invalid `sel_i`, independence from the opcode.

Mutation check: `make mutate M=imm_gen`, 14 mutants (shifted fields and wrong sign bits in every format), all caught.

## Status

`make lint` is clean for XLEN = 64 and 32, `make unit` passes, Yosys synthesizes the module; area in [overview.md](overview.md).
