# Decoder (`rtl/common/decoder.sv`)

Diagram: [img/decoder.svg](../img/decoder.svg). Connections between modules and area: [overview.md](overview.md).

A combinational decoder for RV64I and the M extension: from a 32-bit instruction it produces the datapath control signals. It does not output rd, rs1, rs2: the datapath takes them straight from `inst`. For XLEN = 32, the RV64-only instructions (LD, SD, LWU, W variants including MULW, DIVW, DIVUW, REMW, REMUW, and shifts with `inst[25] = 1`) are illegal.

## Outputs

| Port | Description |
|---|---|
| `alu_op_o` (`alu_op_t`) | ALU operation; for OP-IMM the top bit is cleared except for SRAI; for M instructions `ALU_ADD` (the ALU is not used) |
| `word_o` | 1 only for OP-32 and OP-IMM-32 (ADD, SUB, SLL, SRL, SRA) and for the M W instructions (MULW, DIVW, DIVUW, REMW, REMUW) |
| `imm_sel_o` (`imm_sel_t`) | Immediate format for `imm_gen` |
| `a_sel_o` (`a_sel_t`) | Operand A: `A_RS1`, `A_PC` (AUIPC, BRANCH, JAL), `A_ZERO` (LUI) |
| `b_sel_o` (`b_sel_t`) | Operand B: `B_RS2` or `B_IMM` |
| `wb_sel_o` (`wb_sel_t`) | What to write to rd: `WB_ALU`, `WB_MEM`, `WB_PC4` (JAL, JALR), `WB_MD` (M instructions) |
| `reg_we_o`, `mem_re_o`, `mem_we_o` | Register write, memory read and write |
| `branch_o`, `jal_o`, `jalr_o` | Jump type; the target is computed by the ALU (`pc + imm` or `rs1 + imm`), the BRANCH condition by `branch_unit` |
| `muldiv_o` | 1 for the 8 M instructions in OP (`funct7 = 0000001`) and the 5 in OP-32 (MULW, DIVW, DIVUW, REMW, REMUW); the core waits for `muldiv` until it reports `done` |
| `funct3_o` | `inst[14:12]`: comparison type for `branch_unit`, access size for `lsu`, operation for `muldiv` |
| `csr_o` | A CSR instruction (CSRRW, CSRRS, CSRRC and the immediate forms): `reg_we_o = 1`, `b_sel_o = B_IMM`, `a_sel_o = A_ZERO` for the immediate forms (rs1 is not read). Whether the CSR exists is decided by `csr` (ADR 014) |
| `ecall_o`, `ebreak_o`, `mret_o` | The fixed encodings of ECALL, EBREAK and MRET; WFI is legal and does nothing |
| `fence_i_o` | FENCE.I (its other fields are ignored, as the specification asks) |
| `illegal_o` | 1 if the encoding is illegal or not supported |

An illegal instruction has no side effects: `reg_we_o`, `mem_re_o`, `mem_we_o`, `branch_o`, `jal_o`, `jalr_o`, `muldiv_o` are 0.

## Not supported (illegal)

Other SYSTEM encodings: URET, SRET, SFENCE.VMA, and ECALL, EBREAK or MRET with nonzero fields; SYSTEM with
`funct3 = 4`; MISC-MEM with `funct3` other than 0 and 1. FENCE is legal and does nothing.

## Testing

`tb/unit/test_decoder.py`, `make unit T=decoder`, XLEN = 64 and 32. All 49 RV64I instructions and 13 M instructions with random registers and immediates, the CSR and system instructions, the OP-IMM contract (ADDI does not become SUB), 28 illegal encodings (among them the M `funct7` in other places and MULH-like encodings in OP-32), invariants on 5000 random words (in particular: `wb_sel = WB_MD` if and only if `muldiv`). Mutation check: `make mutate M=decoder`, 18 mutants, 17 caught, 1 equivalent (the clearing of `muldiv_o` for an illegal instruction, which is unreachable anyway).

## Status

`make lint` is clean, `make unit` passes, Yosys synthesizes the module; 47 LUT6 and 125 gates (measured 2026-11-25, Yosys 0.66, XLEN = 64, no commit hash, estimate; before M it was 40 LUT6 and 113 gates).
