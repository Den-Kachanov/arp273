# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/ref/core.sv for tb/unit/test_core.py (see scripts/mutate.py); XLEN = 64 only, to save time"""

RTL = "rtl/ref/core.sv"
TEST = "tb/unit/test_core.py"
KEXPR = "test_core_runner and 64"

MUTANTS = [
    # next pc, jumps, branches
    ("JALR keeps bit 0", "pc_d = {alu_r[X-1:1], 1'b0};", "pc_d = alu_r;"),
    ("branch always taken", "else if (jal || (branch && taken)) pc_d = alu_r;", "else if (jal || branch) pc_d = alu_r;"),
    ("pc + 8", "assign pc_plus4    = pc_q + X'(4);", "assign pc_plus4    = pc_q + X'(8);"),
    ("link is pc, not pc + 4", "riscv_pkg::WB_PC4: wb_data = pc_plus4;", "riscv_pkg::WB_PC4: wb_data = pc_q;"),
    ("AUIPC and jumps use rs1", "riscv_pkg::A_PC:   alu_a = pc_q;", "riscv_pkg::A_PC:   alu_a = rs1_data;"),
    ("immediate ignored", "if (b_sel == riscv_pkg::B_IMM) alu_b = imm;", "if (1'b0) alu_b = imm;"),
    # traps
    ("misaligned data access ignored", "assign trap_o     = illegal | (misaligned & (mem_re | mem_we)) | fetch_misaligned |",
     "assign trap_o     = illegal | fetch_misaligned |"),
    ("ECALL does not stop the reference core", "sys_csr | sys_ecall | sys_ebreak", "sys_csr | sys_ebreak"),
    ("load not suppressed on trap", "assign dmem_re_o    = mem_re & ~trap_o;", "assign dmem_re_o    = mem_re;"),
    ("store not suppressed on trap", "assign dmem_wstrb_o = (mem_we & ~trap_o) ? lsu_wstrb : '0;", "assign dmem_wstrb_o = mem_we ? lsu_wstrb : '0;"),
    ("misaligned jump target ignored", "assign fetch_misaligned = (jal | jalr | (branch & taken)) & alu_r[1];", "assign fetch_misaligned = 1'b0;"),
    ("wrong bit for the target alignment", "(jal | jalr | (branch & taken)) & alu_r[1];", "(jal | jalr | (branch & taken)) & alu_r[2];"),
    ("not-taken branch traps", "(jal | jalr | (branch & taken)) & alu_r[1];", "(jal | jalr | branch) & alu_r[1];"),
    ("JALR target alignment ignored", "(jal | jalr | (branch & taken)) & alu_r[1];", "(jal | (branch & taken)) & alu_r[1];"),
    ("JAL target alignment ignored", "(jal | jalr | (branch & taken)) & alu_r[1];", "(jalr | (branch & taken)) & alu_r[1];"),
    # commit trace
    ("writes to x0 reported", "assign commit_we_o    = reg_we_eff & (rd != 5'd0);", "assign commit_we_o    = reg_we_eff;"),
    # M extension
    ("pc moves while waiting", "if (trap_o || stall)               pc_d = pc_q;", "if (trap_o)                       pc_d = pc_q;"),
    ("register written while waiting", "assign reg_we_eff = reg_we & ~trap_o & ~stall;", "assign reg_we_eff = reg_we & ~trap_o;"),
    ("commit while waiting", "assign commit_valid_o = rst_ni & ~trap_o & ~stall;", "assign commit_valid_o = rst_ni & ~trap_o;"),
    ("stalls not counted", ".stall_i   (rst_ni & stall),", ".stall_i   (1'b0),"),
    ("M result not written back", "\t\t\triscv_pkg::WB_MD:  wb_data = md_result;\n", ""),
    ("stall ignores done", "assign stall      = muldiv & ~md_done;", "assign stall      = muldiv;"),
    ("M operands swapped", ".a_i      (rs1_data),\n\t\t.b_i      (rs2_data),\n\t\t.done_o",
     ".a_i      (rs2_data),\n\t\t.b_i      (rs1_data),\n\t\t.done_o"),
    ("word flag lost", ".word_i   (word),\n\t\t.a_i      (rs1_data),", ".word_i   (1'b0),\n\t\t.a_i      (rs1_data),"),
]

EQUIVALENT = {}
