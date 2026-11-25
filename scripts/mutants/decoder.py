# SPDX-License-Identifier: CERN-OHL-S-2.0
"""Mutants of rtl/core/decoder.sv for tb/unit/test_decoder.py (see scripts/mutate.py)"""

RTL = "rtl/core/decoder.sv"
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
]

EQUIVALENT = {
    "illegal instruction keeps muldiv": "muldiv_o is only set together with illegal_o = 0, so the clearing is defensive",
}
