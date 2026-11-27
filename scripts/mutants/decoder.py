# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/common/decoder.sv for tb/unit/test_decoder.py (see scripts/mutate.py)"""

RTL = "rtl/common/decoder.sv"
TEST = "tb/unit/test_decoder.py"

MUTANTS = [
    # RV64I
    ("SRAI decoded as SRL", "if (shift_hi_sra) alu_op_o = riscv_pkg::ALU_SRA;", ""),
    ("ADDW without the word flag", "word_o   = 1'b1;", "word_o   = 1'b0;"),
    ("LD accepted at XLEN = 32", "3'b011, 3'b110:                         illegal_o = !RV64;",
     "3'b011, 3'b110:                         illegal_o = 1'b0;"),
    ("BRANCH funct3 2 and 3 accepted", "illegal_o = (funct3 == 3'b010 || funct3 == 3'b011);", "illegal_o = 1'b0;"),
    ("JALR not flagged", "jalr_o    = 1'b1;", "jalr_o    = 1'b0;"),
    ("LUI adds rs1", "a_sel_o   = riscv_pkg::A_ZERO;", "a_sel_o   = riscv_pkg::A_RS1;"),
    ("store immediate format", "imm_sel_o = riscv_pkg::IMM_S;", "imm_sel_o = riscv_pkg::IMM_I;"),
    ("branch immediate format", "imm_sel_o = riscv_pkg::IMM_B;", "imm_sel_o = riscv_pkg::IMM_I;"),
    ("jump immediate format", "imm_sel_o = riscv_pkg::IMM_J;", "imm_sel_o = riscv_pkg::IMM_I;"),
    ("store does not write memory", "mem_we_o  = 1'b1;", "mem_we_o  = 1'b0;"),
    ("load writes the ALU result", "wb_sel_o  = riscv_pkg::WB_MEM;", "wb_sel_o  = riscv_pkg::WB_ALU;"),
    ("SLLIW accepted with any funct7", "illegal_o = !(RV64 && funct7 == 7'b0000000);", "illegal_o = !RV64;"),
    # M extension
    ("OP: M not flagged muldiv",
     "wb_sel_o  = riscv_pkg::WB_MD;\n\t\t\t\t\tmuldiv_o  = 1'b1;\n\t\t\t\t\tillegal_o = 1'b0;\n\t\t\t\tend\n\t\t\tend\n\n\t\t\t// RV64 only: ADDW",
     "wb_sel_o  = riscv_pkg::WB_MD;\n\t\t\t\t\tillegal_o = 1'b0;\n\t\t\t\tend\n\t\t\tend\n\n\t\t\t// RV64 only: ADDW"),
    ("OP-32: any funct3 for M", "(funct3 == 3'b000 || funct3[2])", "1'b1"),
    ("OP-32: M legal at XLEN 32", "if (RV64 && funct7 == 7'b0000001 &&", "if (funct7 == 7'b0000001 &&"),
    ("OP-32: M writes the ALU result",
     "alu_op_o  = riscv_pkg::ALU_ADD;\n\t\t\t\t\twb_sel_o  = riscv_pkg::WB_MD;\n\t\t\t\t\tmuldiv_o  = 1'b1;\n\t\t\t\t\tillegal_o = 1'b0;\n\t\t\t\tend\n\t\t\tend\n\n\t\t\t// I-type ALU",
     "alu_op_o  = riscv_pkg::ALU_ADD;\n\t\t\t\t\tmuldiv_o  = 1'b1;\n\t\t\t\t\tillegal_o = 1'b0;\n\t\t\t\tend\n\t\t\tend\n\n\t\t\t// I-type ALU"),
    ("OP: funct7[6] ignored for M", "if (funct7 == 7'b0000001) begin\n\t\t\t\t\talu_op_o  = riscv_pkg::ALU_ADD;\n\t\t\t\t\twb_sel_o  = riscv_pkg::WB_MD;\n\t\t\t\t\tmuldiv_o  = 1'b1;\n\t\t\t\t\tillegal_o = 1'b0;\n\t\t\t\tend\n\t\t\tend\n\n\t\t\t// RV64 only: ADDW",
     "if (funct7[5:0] == 6'b000001) begin\n\t\t\t\t\talu_op_o  = riscv_pkg::ALU_ADD;\n\t\t\t\t\twb_sel_o  = riscv_pkg::WB_MD;\n\t\t\t\t\tmuldiv_o  = 1'b1;\n\t\t\t\t\tillegal_o = 1'b0;\n\t\t\t\tend\n\t\t\tend\n\n\t\t\t// RV64 only: ADDW"),
    ("illegal instruction keeps muldiv", "muldiv_o = 1'b0;", ""),
    # A extension (ADR 019)
    ("AMO not flagged", "amo_o    = 1'b1;", "amo_o    = 1'b0;"),
    ("LR writes memory", "mem_we_o = (inst_i[31:27] != riscv_pkg::AMO_LR);", "mem_we_o = 1'b1;"),
    ("LR reads rs2", "b_sel_o  = (inst_i[31:27] == riscv_pkg::AMO_LR) ? riscv_pkg::B_IMM : riscv_pkg::B_RS2;",
     "b_sel_o  = riscv_pkg::B_RS2;"),
    ("LR with rs2 != 0 accepted", "riscv_pkg::AMO_LR:  illegal_o = (inst_i[24:20] != 5'd0);",
     "riscv_pkg::AMO_LR:  illegal_o = 1'b0;"),
    ("AMOMAXU not decoded", "riscv_pkg::AMO_MINU, riscv_pkg::AMO_MAXU: illegal_o", "riscv_pkg::AMO_MINU: illegal_o"),
    (".D accepted at XLEN = 32", "(RV64 && funct3 == 3'b011)", "(funct3 == 3'b011)"),
    ("AMO with any funct3", "if (!(funct3 == 3'b010 || (RV64 && funct3 == 3'b011)))", "if (1'b0)"),
    ("AMO result from the ALU", "amo_o    = 1'b1;\n\t\t\t\twb_sel_o = riscv_pkg::WB_MEM;", "amo_o    = 1'b1;"),
    ("WFI not flagged", "32'h1050_0073: begin wfi_o    = 1'b1; illegal_o = 1'b0; end",
     "32'h1050_0073: begin illegal_o = 1'b0; end"),
    ("illegal instruction keeps amo", "csr_o    = 1'b0;\n\t\t\tamo_o    = 1'b0;", "csr_o    = 1'b0;"),
]

EQUIVALENT = {
    "illegal instruction keeps muldiv": "muldiv_o is only set together with illegal_o = 0, so the clearing is defensive",
}
