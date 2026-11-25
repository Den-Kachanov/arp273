# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/core/core_pipe.sv for the pipeline tests (see scripts/mutate.py); XLEN = 64 only, to save time"""

RTL = "rtl/core/core_pipe.sv"
TEST = ["tb/unit/test_pipeline.py", "tb/unit/test_core.py"]
KEXPR = "pipe and 64"

MUTANTS = [
    # forwarding
    ("no forwarding from MEM", "assign mem_fwd_ok      = mem_valid_q & mem_reg_we_q & ~mem_mem_re_q & (mem_rd_q != 5'd0);",
     "assign mem_fwd_ok      = 1'b0;"),
    ("no forwarding from WB", "assign wb_fwd_ok       = wb_valid_q & wb_reg_we_q & (wb_rd_q != 5'd0);", "assign wb_fwd_ok       = 1'b0;"),
    ("x0 forwarded from MEM", "assign mem_fwd_ok      = mem_valid_q & mem_reg_we_q & ~mem_mem_re_q & (mem_rd_q != 5'd0);",
     "assign mem_fwd_ok      = mem_valid_q & mem_reg_we_q & ~mem_mem_re_q;"),
    ("x0 forwarded from WB", "assign wb_fwd_ok       = wb_valid_q & wb_reg_we_q & (wb_rd_q != 5'd0);",
     "assign wb_fwd_ok       = wb_valid_q & wb_reg_we_q;"),
    ("WB before MEM (older value wins)", "if (mem_fwd_ok && mem_rd_q == ex_rs1_q)                       ex_rs1 = mem_result_q;",
     "if (mem_fwd_ok && mem_rd_q == ex_rs1_q && !(wb_fwd_ok && wb_rd_q == ex_rs1_q)) ex_rs1 = mem_result_q;"),
    ("rs2 not forwarded from MEM", "if (mem_fwd_ok && mem_rd_q == ex_rs2_q)                       ex_rs2 = mem_result_q;",
     "if (1'b0)                       ex_rs2 = mem_result_q;"),
    ("store data not forwarded", "mem_store_q   <= ex_rs2;", "mem_store_q   <= ex_rs2_data_q;"),
    ("operands not refreshed while EX waits", "if (!ex_rs1_wait) ex_rs1_data_q <= ex_rs1;", ""),
    ("rs2 not refreshed while EX waits", "if (!ex_rs2_wait) ex_rs2_data_q <= ex_rs2;", ""),
    ("register file without write-through", "regfile #(.WRITE_THROUGH(1'b1)) u_regfile", "regfile #(.WRITE_THROUGH(1'b0)) u_regfile"),
    # load-use
    ("no load wait in EX", "		                                                              ex_rs1_wait = 1'b1;\n",
     "		                                                              ex_rs1_wait = 1'b0;\n"),
    ("no wait for a pending WB load", "assign wb_load_pending = wb_valid_q & wb_mem_re_q & ~dmem_rsp_valid_i;",
     "assign wb_load_pending = 1'b0;"),
    # fetch and redirect
    ("redirect keeps the fetch queue", "assign fq_occ_next  = redirect ? 2'd0 : (fq_cnt_q + {1'b0, fq_push} - {1'b0, fq_pop});",
     "assign fq_occ_next  = fq_cnt_q + {1'b0, fq_push} - {1'b0, fq_pop};"),
    ("in-flight response not dropped", "drop_q   <= redirect ? (out_q & ~imem_rsp_valid_i) : (drop_q & ~imem_rsp_valid_i);",
     "drop_q   <= 1'b0;"),
    ("ID not flushed on redirect", "assign id_advance = id_valid & ~redirect & (~ex_valid_q | ex_advance);",
     "assign id_advance = id_valid & (~ex_valid_q | ex_advance);"),
    ("JALR keeps bit 0", "assign redirect_pc = ex_jalr_q ? {alu_r[X-1:1], 1'b0} : alu_r;", "assign redirect_pc = alu_r;"),
    ("target + 4 is the target", "assign redirect_pc4 = ex_jalr_q ? {tgt4[X-1:1], 1'b0} : tgt4;", "assign redirect_pc4 = redirect_pc;"),
    ("JALR target + 4 from pc", "assign tgt4         = (ex_jalr_q ? ex_rs1 : ex_pc_q) + ex_imm4_q;", "assign tgt4         = ex_pc_q + ex_imm4_q;"),
    ("link is pc", "if (ex_jal_q || ex_jalr_q) ex_result = ex_pc_q + X'(4);", "if (ex_jal_q || ex_jalr_q) ex_result = ex_pc_q;"),
    ("two requests outstanding", "assign fetch_issue  = rst_ni & (~out_q | imem_rsp_valid_i) & (fq_occ_next <= 2'd1);",
     "assign fetch_issue  = rst_ni & (fq_occ_next <= 2'd1);"),
    ("queue overfills", "(fq_occ_next <= 2'd1);", "(fq_occ_next <= 2'd2);"),
    ("push into the wrong entry", "if (fq_cnt_q - {1'b0, fq_pop} == 2'd0) begin", "if (fq_cnt_q == 2'd0) begin"),
    # multiplier/divider
    ("M result not kept when MEM is busy", "md_have_q <= 1'b1;", "md_have_q <= 1'b0;"),
    ("M operands not checked", "assign md_valid = ex_valid_q & ex_muldiv_q & ~md_have_q & (md_busy_q | ex_ops_ready);",
     "assign md_valid = ex_valid_q & ex_muldiv_q & ~md_have_q;"),
    # memory port and WB
    ("data request without room in WB", "assign dmem_req_valid_o = mem_valid_q & mem_access & ~mem_trap & wb_can_accept;",
     "assign dmem_req_valid_o = mem_valid_q & mem_access & ~mem_trap;"),
    ("misaligned access goes out", "assign dmem_req_valid_o = mem_valid_q & mem_access & ~mem_trap & wb_can_accept;",
     "assign dmem_req_valid_o = mem_valid_q & mem_access & wb_can_accept;"),
    ("stores do not wait for their response", "assign wb_wait       = (wb_mem_re_q | wb_mem_we_q) & ~wb_trap_q & ~dmem_rsp_valid_i;",
     "assign wb_wait       = wb_mem_re_q & ~wb_trap_q & ~dmem_rsp_valid_i;"),
    ("trap does not freeze WB", "assign wb_advance    = wb_valid_q & ~wb_wait & ~wb_trap_q;", "assign wb_advance    = wb_valid_q & ~wb_wait;"),
    ("trapping jump redirects", "assign redirect    = ex_advance & ex_ctrl_taken & ~ex_trap;", "assign redirect    = ex_advance & ex_ctrl_taken;"),
    ("load data not used", "assign wb_data = wb_mem_re_q ? wb_load_data : wb_result_q;", "assign wb_data = wb_result_q;"),
    ("flushes not counted", ".flush_i   (redirect),", ".flush_i   (1'b0),"),
]

EQUIVALENT = {
    "M result not kept when MEM is busy": "without the hold the unit returns to idle, sees valid and computes the "
                                          "same result again: only time is lost while the memory stalls",
}
