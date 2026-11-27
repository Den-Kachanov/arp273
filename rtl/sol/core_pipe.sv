// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

// Five-stage pipelined core, RV64IMA (RV32IMA for XLEN = 32), ADR 013, ADR 019
//
//   IF   fetch: requests to the instruction port, responses into a 2-entry fetch queue
//   ID   decode, register read (the head of the fetch queue is the ID instruction)
//   EX   ALU, branch decision, jump targets, forwarding, multiplier/divider
//   MEM  request to the data port (address, store data and strobes)
//   WB   load response, register write, commit
//
// Memory ports (ADR 013): valid/ready request, response at least one cycle later, in order,
// one outstanding request per port, a response for every request (stores too). An atomic of the A
// extension is one data request with dmem_req_amo_o set and its operation on dmem_req_atop_o; the
// memory side executes it (the model now, the L2 later, ADR 019) and responds with the old value
// (LR, AMOs) or the SC result (0 = stored, 1 = failed), in the lane of the access
//
// Hazards:
//   forwarding from MEM (not loads) and from WB into EX, never for x0
//   load-use: the consumer waits in EX one cycle while the load is in MEM (its data arrives in WB)
//   taken branches and jumps are decided in EX: ID and the fetch queue are flushed and a
//   response still on the way is dropped
//   the multiplier/divider holds EX until done; the result is kept if MEM cannot take it yet
//   a trap reaches WB and freezes the pipeline: younger instructions write nothing
//
// Interrupts (ADR 019): taken in WB in place of the instruction there, which then has not executed
// (mepc = its pc), like an exception. Not on a load, store or atomic: its request has already gone to
// memory, so it commits and the next instruction takes the interrupt. WFI waits in WB until an
// interrupt is pending and enabled in mie, then commits; with MIE set the next instruction takes the
// interrupt (mepc = WFI + 4, as in Spike when the interrupt arrives during the wait). WFI itself is never
// replaced: `while (!flag) wfi();` must re-check the flag after the handler

module core_pipe #(
	parameter logic [riscv_pkg::XLEN-1:0] RESET_PC = riscv_pkg::XLEN'(64'h8000_0000),
	// mhartid (ADR 019)
	parameter int HART_ID = 0,
	// 1: verification build. 0: release build: everything that exists only to check the core is
	// removed: the commit trace (the instruction carried through ID/EX, EX/MEM and MEM/WB, the copies
	// of store data and strobes in WB, all commit_* outputs read 0) and the stall and flush
	// counters (stalls_o and flushes_o read 0). The cycle and instret counters stay: they become
	// architectural CSRs. The behaviour on the memory ports does not depend on DEBUG
	parameter bit DEBUG = 1'b1,
	// multiplier bits per cycle of the M unit (1, 2, 4, 8; ADR 017): 4 = Sol 2, 1 = Sol 1
	parameter int MUL_BITS = 4,
	// branch prediction in IF (bpred, ADR 018): measured and not enabled by default (0 = Sol 1 and Sol 2,
	// cycle for cycle without it; 1 = with the predictor); the sizes
	// are the predictor's parameters
	parameter bit BP              = 1'b0,
	parameter int BHT_ENTRIES     = 64,
	parameter int BTB_ENTRIES     = 16,
	parameter int RAS_DEPTH       = 8,
	parameter int BTB_TAG_BITS    = 10,
	parameter int BTB_TARGET_BITS = 20
) (
	input  logic                          clk_i,
	input  logic                          rst_ni,

	// instruction port
	output logic                          imem_req_valid_o,
	input  logic                          imem_req_ready_i,
	output logic [riscv_pkg::XLEN-1:0]    imem_req_addr_o,
	input  logic                          imem_rsp_valid_i,
	input  logic [31:0]                   imem_rsp_data_i,

	// data port, XLEN bits wide, aligned
	output logic                          dmem_req_valid_o,
	input  logic                          dmem_req_ready_i,
	output logic [riscv_pkg::XLEN-1:0]    dmem_req_addr_o,
	output logic                          dmem_req_we_o,
	output logic [riscv_pkg::XLEN/8-1:0]  dmem_req_wstrb_o,
	output logic [riscv_pkg::XLEN-1:0]    dmem_req_wdata_o,
	// atomic request: the operation (funct5 of the A extension, riscv_pkg::amo_op_t); for these
	// dmem_req_wstrb_o gives the lanes of the access, LR included
	output logic                          dmem_req_amo_o,
	output logic [4:0]                    dmem_req_atop_o,
	input  logic                          dmem_rsp_valid_i,
	input  logic [riscv_pkg::XLEN-1:0]    dmem_rsp_data_i,

	// interrupt lines, levels: CLINT (software, timer) and PLIC (external)
	input  logic                          irq_msip_i,
	input  logic                          irq_mtip_i,
	input  logic                          irq_meip_i,

	// a trap (exception or interrupt) is taken in WB; commit_pc_o shows the instruction it replaces
	output logic                          trap_o,

	// commit trace, from WB
	output logic                          commit_valid_o,
	output logic [riscv_pkg::XLEN-1:0]    commit_pc_o,
	output logic [31:0]                   commit_inst_o,
	output logic                          commit_we_o,
	output logic [4:0]                    commit_rd_o,
	output logic [riscv_pkg::XLEN-1:0]    commit_data_o,
	// the memory access of the committed instruction, for the commit log
	output logic                          commit_mem_re_o,
	output logic                          commit_mem_we_o,
	output logic [riscv_pkg::XLEN-1:0]    commit_mem_addr_o,
	output logic [riscv_pkg::XLEN-1:0]    commit_mem_wdata_o,
	output logic [riscv_pkg::XLEN/8-1:0]  commit_mem_wstrb_o,

	// performance counters: cycles, retired instructions, stalls (cycles with a valid instruction
	// held in ID), flushes (taken branches and jumps)
	output logic [63:0]                   perf_cycles_o,
	output logic [63:0]                   perf_instret_o,
	output logic [63:0]                   perf_stalls_o,
	output logic [63:0]                   perf_flushes_o
);

	localparam int X = riscv_pkg::XLEN;
	localparam int S = X / 8;

	// ------------------------------------------------------------------ IF
	logic [X-1:0] pc_f_q;            // next fetch address
	logic         out_q;             // a request was accepted, its response is pending
	logic [X-1:0] out_pc_q;          // address of that request
	logic         drop_q;            // the pending response belongs to a flushed path
	logic [1:0]   fq_cnt_q;          // fetch queue: entry 0 is the oldest (the ID instruction)
	logic [X-1:0] fq_pc_q   [2];
	logic [31:0]  fq_inst_q [2];

	logic         redirect;          // the front end is flushed: a taken branch or jump in EX, or WB
	logic         ex_redirect;       // a taken branch or jump in EX
	logic         wb_redirect;       // WB takes an exception, or retires MRET or FENCE.I (ADR 014)
	logic [X-1:0] wb_target;         // mtvec, mepc, or pc + 4 after FENCE.I
	logic [X-1:0] redirect_pc;
	logic [X-1:0] redirect_pc4;      // redirect_pc + 4, computed in parallel (see EX)
	logic         fq_push;
	logic         fq_pop;            // ID hands its instruction to EX
	logic [1:0]   fq_occ_next;       // occupancy after this cycle, before a new request
	logic         fetch_issue;
	logic         fetch_accept;
	logic [X-1:0] fetch_addr;
	logic [X-1:0] fe_target;         // where an EX redirect fetches: the target, or pc + 4 when a
	logic [X-1:0] fe_target4;        // predicted-taken instruction falls through; and that + 4

	// branch prediction (BP = 1, ADR 018): the guess for the fetch at pc_f_q. It travels with the
	// instruction (taken bit and the low target bits; the upper bits are those of the pc) to EX,
	// which checks it. The fetch at an EX redirect target is not predicted, so the lookup stays off
	// the redirect path
	localparam int TB = BTB_TARGET_BITS;
	localparam int TW = TB - 2;
	logic          bp_taken;
	logic [X-1:0]  bp_target;
	logic          f_pred;           // this fetch is predicted taken
	logic          out_pred_q;
	logic [TW-1:0] out_ptgt_q;
	logic          fq_pred_q [2];
	logic [TW-1:0] fq_ptgt_q [2];

	assign f_pred = BP & ~ex_redirect & bp_taken;

	assign fq_push      = imem_rsp_valid_i & out_q & ~drop_q & ~redirect;
	assign fq_occ_next  = redirect ? 2'd0 : (fq_cnt_q + {1'b0, fq_push} - {1'b0, fq_pop});
	// one outstanding request; a new one may start in the cycle the previous response arrives,
	// and only if the queue will have room for its response. On a redirect from EX the target is
	// fetched in the same cycle when the port allows it: a taken branch then costs two cycles, not
	// three. A redirect from WB (rare: exceptions, MRET, FENCE.I) fetches one cycle later, so that
	// no mux and adder follow the WB decision in the same cycle
	assign redirect     = ex_redirect | wb_redirect;
	assign fetch_addr   = ex_redirect ? fe_target : pc_f_q;
	assign fetch_issue  = rst_ni & ~wb_redirect & (~out_q | imem_rsp_valid_i) & (fq_occ_next <= 2'd1);
	assign fetch_accept = fetch_issue & imem_req_ready_i;

	assign imem_req_valid_o = fetch_issue;
	assign imem_req_addr_o  = fetch_addr;

	always_ff @(posedge clk_i) begin
		if (!rst_ni) begin
			pc_f_q   <= RESET_PC;
			out_q    <= 1'b0;
			drop_q   <= 1'b0;
			fq_cnt_q <= '0;
		end else begin
			// no adder after the redirect decision: the target + 4 comes ready from EX, so the
			// branch compare is not followed by another carry chain in the same cycle
			if (wb_redirect)      pc_f_q <= wb_target;
			else if (fetch_accept) pc_f_q <= ex_redirect ? fe_target4 : (f_pred ? bp_target : pc_f_q + X'(4));
			else if (ex_redirect)  pc_f_q <= fe_target;

			out_q    <= fetch_accept | (out_q & ~imem_rsp_valid_i);
			// a response still on the way when the path changes is dropped when it arrives (a request
			// on the new path can only start once it has arrived: one outstanding request)
			drop_q   <= redirect ? (out_q & ~imem_rsp_valid_i) : (drop_q & ~imem_rsp_valid_i);
			fq_cnt_q <= fq_occ_next;

			// queue contents: pop shifts entry 1 down, push writes the first free entry
			if (fq_pop) begin
				fq_pc_q[0]   <= fq_pc_q[1];
				fq_inst_q[0] <= fq_inst_q[1];
				fq_pred_q[0] <= fq_pred_q[1];
				fq_ptgt_q[0] <= fq_ptgt_q[1];
			end
			if (fq_push) begin
				if (fq_cnt_q - {1'b0, fq_pop} == 2'd0) begin
					fq_pc_q[0]   <= out_pc_q;
					fq_inst_q[0] <= imem_rsp_data_i;
					fq_pred_q[0] <= out_pred_q;
					fq_ptgt_q[0] <= out_ptgt_q;
				end else begin
					fq_pc_q[1]   <= out_pc_q;
					fq_inst_q[1] <= imem_rsp_data_i;
					fq_pred_q[1] <= out_pred_q;
					fq_ptgt_q[1] <= out_ptgt_q;
				end
			end
		end
		if (fetch_accept) begin
			out_pc_q   <= fetch_addr;
			out_pred_q <= f_pred;
			out_ptgt_q <= bp_target[TB-1:2];
		end
	end

	// ------------------------------------------------------------------ ID
	logic                id_valid;
	logic [X-1:0]        id_pc;
	logic [31:0]         id_inst;
	riscv_pkg::alu_op_t  id_alu_op;
	logic                id_word;
	riscv_pkg::imm_sel_t id_imm_sel;
	riscv_pkg::a_sel_t   id_a_sel;
	riscv_pkg::b_sel_t   id_b_sel;
	riscv_pkg::wb_sel_t  id_wb_sel;
	logic                id_reg_we, id_mem_re, id_mem_we, id_branch, id_jal, id_jalr, id_muldiv, id_illegal;
	logic                id_csr, id_ecall, id_ebreak, id_mret, id_wfi, id_fence_i, id_amo;
	logic                id_csr_write;
	logic [2:0]          id_funct3;
	logic [X-1:0]        id_rs1_data, id_rs2_data, id_imm;
	logic [X-1:0]        id_imm4;
	logic                id_use_rs1, id_use_rs2;
	logic                id_advance;

	assign id_valid = (fq_cnt_q != 2'd0);
	assign id_pc    = fq_pc_q[0];
	assign id_inst  = fq_inst_q[0];

	decoder u_decoder (
		.inst_i    (id_inst),
		.alu_op_o  (id_alu_op),
		.word_o    (id_word),
		.imm_sel_o (id_imm_sel),
		.a_sel_o   (id_a_sel),
		.b_sel_o   (id_b_sel),
		.wb_sel_o  (id_wb_sel),
		.reg_we_o  (id_reg_we),
		.mem_re_o  (id_mem_re),
		.mem_we_o  (id_mem_we),
		.branch_o  (id_branch),
		.jal_o     (id_jal),
		.jalr_o    (id_jalr),
		.muldiv_o  (id_muldiv),
		.csr_o     (id_csr),
		.ecall_o   (id_ecall),
		.ebreak_o  (id_ebreak),
		.mret_o    (id_mret),
		.wfi_o     (id_wfi),
		.fence_i_o (id_fence_i),
		.amo_o     (id_amo),
		.funct3_o  (id_funct3),
		.illegal_o (id_illegal)
	);

	imm_gen u_imm_gen (
		.inst_i (id_inst),
		.sel_i  (id_imm_sel),
		.imm_o  (id_imm)
	);

	// imm + 4 for the "target + 4" adder in EX, added here where there is time
	assign id_imm4 = id_imm + X'(4);

	// CSRRW always writes the CSR; CSRRS, CSRRC and the immediate forms only with a nonzero rs1
	// field (rs1 = x0 or uimm = 0 only reads, so a read-only CSR can be read)
	assign id_csr_write = (id_funct3[1:0] == 2'b01) | (id_inst[19:15] != 5'd0);

	// WB writes and ID reads in the same cycle: the read returns the new value (ADR 004)
	logic         wb_we;
	logic [4:0]   wb_rd;
	logic [X-1:0] wb_data;

	regfile #(.WRITE_THROUGH(1'b1)) u_regfile (
		.clk_i    (clk_i),
		.we_i     (wb_we),
		.waddr_i  (wb_rd),
		.wdata_i  (wb_data),
		.raddr1_i (id_inst[19:15]),
		.raddr2_i (id_inst[24:20]),
		.rdata1_o (id_rs1_data),
		.rdata2_o (id_rs2_data)
	);

	// registers an instruction really reads (a false match would only cost a stall)
	assign id_use_rs1 = (id_a_sel == riscv_pkg::A_RS1) | id_branch;
	assign id_use_rs2 = (id_b_sel == riscv_pkg::B_RS2) | id_branch | id_mem_we;

	// ------------------------------------------------------------------ ID/EX register
	logic                ex_valid_q;
	logic [X-1:0]        ex_pc_q;
	logic [31:0]         ex_inst_q;
	riscv_pkg::alu_op_t  ex_alu_op_q;
	logic                ex_word_q;
	riscv_pkg::a_sel_t   ex_a_sel_q;
	riscv_pkg::b_sel_t   ex_b_sel_q;
	riscv_pkg::wb_sel_t  ex_wb_sel_q;
	logic                ex_reg_we_q, ex_mem_re_q, ex_mem_we_q, ex_branch_q, ex_jal_q, ex_jalr_q, ex_muldiv_q;
	logic                ex_illegal_q;
	logic                ex_csr_q, ex_csr_write_q, ex_ecall_q, ex_ebreak_q, ex_mret_q, ex_wfi_q, ex_fence_i_q, ex_amo_q;
	logic [2:0]          ex_funct3_q;
	logic [4:0]          ex_rd_q, ex_rs1_q, ex_rs2_q;
	logic                ex_use_rs1_q, ex_use_rs2_q;
	logic [X-1:0]        ex_rs1_data_q, ex_rs2_data_q, ex_imm_q, ex_imm4_q;
	logic                ex_pred_q;        // fetched as predicted taken (BP)
	logic [TW-1:0]       ex_ptgt_q;        // low bits of the predicted target

	// ------------------------------------------------------------------ EX/MEM and MEM/WB registers
	logic                mem_valid_q;
	logic [X-1:0]        mem_pc_q;
	logic [31:0]         mem_inst_q;
	logic                mem_reg_we_q, mem_mem_re_q, mem_mem_we_q, mem_trap_in_q;
	logic [2:0]          mem_funct3_q;
	logic [4:0]          mem_rd_q;
	logic [X-1:0]        mem_result_q;     // ALU result, link address or M result; the address for loads and stores;
	                                       // the CSR write value; mtval of a trap
	logic [X-1:0]        mem_store_q;
	logic                mem_csr_q, mem_csr_write_q, mem_mret_q, mem_wfi_q, mem_fence_i_q, mem_amo_q;
	logic [3:0]          mem_cause_q;      // exception cause of a trap from ID or EX
	logic [16:0]         mem_sys_q;        // CSR address and rs1 field (instruction bits 31:15); the AMO
	                                       // operation is bits 31:27

	logic                wb_valid_q;
	logic [X-1:0]        wb_pc_q;
	logic [31:0]         wb_inst_q;
	logic                wb_reg_we_q, wb_mem_re_q, wb_mem_we_q, wb_trap_q;
	logic                wb_csr_q, wb_csr_write_q, wb_mret_q, wb_wfi_q, wb_fence_i_q;
	logic [3:0]          wb_cause_q;
	logic [16:0]         wb_sys_q;
	logic [2:0]          wb_funct3_q;
	logic [4:0]          wb_rd_q;
	logic [X-1:0]        wb_result_q;
	logic [X-1:0]        wb_wdata_q;
	logic [S-1:0]        wb_wstrb_q;

	// ------------------------------------------------------------------ EX
	logic [X-1:0] ex_rs1, ex_rs2;      // operands after forwarding
	logic         ex_rs1_wait, ex_rs2_wait;
	logic         mem_fwd_ok;
	logic         wb_fwd_ok;
	logic         wb_load_pending;
	logic [X-1:0] alu_a, alu_b, alu_r;
	logic         taken;
	logic         ex_ctrl_taken;
	logic         ex_fetch_misaligned;
	logic         ex_trap;
	logic [3:0]   ex_cause;
	logic [X-1:0] ex_result;
	logic [X-1:0] ex_pc4;            // pc + 4: the link value, and where a wrongly predicted fall-through goes
	logic         flush;             // WB redirects: every younger instruction is dropped
	logic         ex_ops_ready;
	logic         ex_done;
	logic         ex_advance;
	logic         mem_can_accept;

	// sources: MEM (not a load or a CSR read: their data comes in WB), then WB, then the ID/EX register
	logic mem_late;
	assign mem_late        = mem_mem_re_q | mem_csr_q;
	assign mem_fwd_ok      = mem_valid_q & mem_reg_we_q & ~mem_late & (mem_rd_q != 5'd0);
	assign wb_fwd_ok       = wb_valid_q & wb_reg_we_q & (wb_rd_q != 5'd0);
	assign wb_load_pending = wb_valid_q & wb_mem_re_q & ~dmem_rsp_valid_i;

	always_comb begin
		ex_rs1      = ex_rs1_data_q;
		ex_rs2      = ex_rs2_data_q;
		ex_rs1_wait = 1'b0;
		ex_rs2_wait = 1'b0;

		if (mem_fwd_ok && mem_rd_q == ex_rs1_q)                       ex_rs1 = mem_result_q;
		else if (mem_valid_q && mem_late && mem_reg_we_q && mem_rd_q != 5'd0 && mem_rd_q == ex_rs1_q)
		                                                              ex_rs1_wait = 1'b1;
		else if (wb_fwd_ok && wb_rd_q == ex_rs1_q) begin
			ex_rs1      = wb_data;
			ex_rs1_wait = wb_load_pending;
		end

		if (mem_fwd_ok && mem_rd_q == ex_rs2_q)                       ex_rs2 = mem_result_q;
		else if (mem_valid_q && mem_late && mem_reg_we_q && mem_rd_q != 5'd0 && mem_rd_q == ex_rs2_q)
		                                                              ex_rs2_wait = 1'b1;
		else if (wb_fwd_ok && wb_rd_q == ex_rs2_q) begin
			ex_rs2      = wb_data;
			ex_rs2_wait = wb_load_pending;
		end
	end

	assign ex_ops_ready = ~(ex_use_rs1_q & ex_rs1_wait) & ~(ex_use_rs2_q & ex_rs2_wait);

	always_comb begin
		alu_a = ex_rs1;
		alu_b = ex_rs2;

		case (ex_a_sel_q)
			riscv_pkg::A_PC:   alu_a = ex_pc_q;
			riscv_pkg::A_ZERO: alu_a = '0;
			default:           alu_a = ex_rs1;
		endcase // ex_a_sel_q

		if (ex_b_sel_q == riscv_pkg::B_IMM) alu_b = ex_imm_q;
	end

	alu u_alu (
		.a_i    (alu_a),
		.b_i    (alu_b),
		.op_i   (ex_alu_op_q),
		.word_i (ex_word_q),
		.r_o    (alu_r)
	);

	branch_unit u_branch_unit (
		.a_i      (ex_rs1),
		.b_i      (ex_rs2),
		.funct3_i (ex_funct3_q),
		.taken_o  (taken)
	);

	// a jump or taken branch to a target with bit 1 set is an instruction-address-misaligned trap
	// on the jump itself (no C extension); it does not redirect
	assign ex_ctrl_taken       = ex_jal_q | ex_jalr_q | (ex_branch_q & taken);
	assign ex_fetch_misaligned = ex_ctrl_taken & alu_r[1];
	assign ex_trap             = ex_illegal_q | ex_fetch_misaligned | ex_ecall_q | ex_ebreak_q;

	// exception causes (privileged specification): the decoder flags at most one of these
	always_comb begin
		ex_cause = 4'd0;                              // instruction address misaligned
		if (ex_illegal_q)     ex_cause = 4'd2;
		else if (ex_ebreak_q) ex_cause = 4'd3;
		else if (ex_ecall_q)  ex_cause = 4'd11;       // ECALL from M-mode
	end

	// multiplier/divider: valid is held from the start until done; the result is kept in md_res_q
	// when MEM cannot take it in the done cycle
	logic         md_valid, md_done, md_busy_q, md_have_q;
	logic [X-1:0] md_result, md_res_q;

	assign md_valid = ex_valid_q & ex_muldiv_q & ~md_have_q & (md_busy_q | ex_ops_ready);

	muldiv #(.MUL_BITS(MUL_BITS)) u_muldiv (
		.clk_i    (clk_i),
		.rst_ni   (rst_ni),
		.valid_i  (md_valid),
		.abort_i  (flush),
		.funct3_i (ex_funct3_q),
		.word_i   (ex_word_q),
		.a_i      (ex_rs1),
		.b_i      (ex_rs2),
		.done_o   (md_done),
		.result_o (md_result)
	);

	// the value EX passes on. For a trap it is mtval: the instruction bits of an illegal instruction
	// (ID put them in ex_imm_q), the target of a misaligned jump, the pc of EBREAK, 0 for ECALL.
	// For a CSR instruction it is the write value: rs1, or the zero-extended 5-bit immediate. For an
	// atomic it is the address: rs1, without an offset
	always_comb begin
		ex_result = alu_r;
		if (ex_illegal_q)                           ex_result = ex_imm_q;
		else if (ex_fetch_misaligned)               ex_result = redirect_pc;
		else if (ex_ebreak_q)                       ex_result = ex_pc_q;
		else if (ex_ecall_q)                        ex_result = '0;
		else if (ex_csr_q)                          ex_result = ex_funct3_q[2] ? X'(ex_imm_q[19:15]) : ex_rs1;
		else if (ex_amo_q)                          ex_result = ex_rs1;
		else if (ex_jal_q || ex_jalr_q || ex_fence_i_q) ex_result = ex_pc4;
		else if (ex_muldiv_q)                       ex_result = md_have_q ? md_res_q : md_result;
	end

	assign ex_done    = ex_ops_ready & (~ex_muldiv_q | md_have_q | md_done);
	assign ex_advance = ex_valid_q & ex_done & mem_can_accept;

	assign redirect_pc = ex_jalr_q ? {alu_r[X-1:1], 1'b0} : alu_r;

	// a redirect from EX corrects the fetch: without prediction after every taken transfer; with it
	// when the direction was wrong, when the target was wrong, or when an instruction that is no
	// transfer at all was predicted taken (a stale or aliased BTB entry, ADR 018)
	logic ex_pred_tgt_ok;
	logic ex_mispredict;

	assign ex_pc4         = ex_pc_q + X'(4);
	assign ex_pred_tgt_ok = (redirect_pc[TB-1:2] == ex_ptgt_q) & (redirect_pc[X-1:TB] == ex_pc_q[X-1:TB]);
	assign ex_mispredict  = BP ? (ex_ctrl_taken ? ~(ex_pred_q & ex_pred_tgt_ok) : ex_pred_q) : ex_ctrl_taken;
	assign ex_redirect    = ex_advance & ex_mispredict & ~ex_trap;

	// target + 4 = base + (imm + 4), in parallel with the ALU; for JALR bit 0 is cleared after the
	// add, which gives the same result because 4 does not touch bit 0
	logic [X-1:0] tgt4;
	assign tgt4         = (ex_jalr_q ? ex_rs1 : ex_pc_q) + ex_imm4_q;
	assign redirect_pc4 = ex_jalr_q ? {tgt4[X-1:1], 1'b0} : tgt4;

	// predicted taken but not taken: continue at pc + 4 (and pc + 8 for the fetch after it)
	assign fe_target  = (BP && !ex_ctrl_taken) ? ex_pc4 : redirect_pc;
	assign fe_target4 = (BP && !ex_ctrl_taken) ? ex_pc_q + X'(8) : redirect_pc4;

	// predictor update: a control instruction leaving EX that is not dropped. A jump that traps on its
	// misaligned target is learned too, harmlessly: the BTB drops target bits 1:0, so a later guess
	// fetches an aligned word, and the trap in WB drops that path anyway (mutation check, ADR 018).
	// Kinds as in bpred: a link register (x1 or x5) as rd makes a call, a JALR from one with rd = x0 a return
	logic       bp_upd;
	logic [1:0] bp_kind;
	logic       rd_link, rs1_link;

	assign rd_link  = (ex_rd_q == 5'd1) | (ex_rd_q == 5'd5);
	assign rs1_link = (ex_rs1_q == 5'd1) | (ex_rs1_q == 5'd5);
	assign bp_upd   = ex_advance & ~flush & (ex_branch_q | ex_jal_q | ex_jalr_q);
	assign bp_kind  = ex_branch_q ? 2'd0 :
	                  rd_link ? 2'd2 :
	                  (ex_jalr_q & rs1_link & (ex_rd_q == 5'd0)) ? 2'd3 : 2'd1;

	generate
		if (BP) begin : g_bp
			bpred #(
				.BHT_ENTRIES (BHT_ENTRIES),
				.BTB_ENTRIES (BTB_ENTRIES),
				.RAS_DEPTH   (RAS_DEPTH),
				.TAG_BITS    (BTB_TAG_BITS),
				.TARGET_BITS (BTB_TARGET_BITS)
			) u_bpred (
				.clk_i        (clk_i),
				.rst_ni       (rst_ni),
				.pc_i         (pc_f_q),
				.taken_o      (bp_taken),
				.target_o     (bp_target),
				.accept_i     (fetch_accept & ~ex_redirect),
				.upd_i        (bp_upd),
				.upd_pc_i     (ex_pc_q),
				.upd_kind_i   (bp_kind),
				.upd_taken_i  (ex_ctrl_taken),
				.upd_target_i (redirect_pc)
			);
		end else begin : g_no_bp
			logic unused_bp;

			assign bp_taken  = 1'b0;
			assign bp_target = '0;
			assign unused_bp = &{1'b0, bp_upd, bp_kind, ex_pred_tgt_ok, ex_pred_q, ex_ptgt_q};
		end
	endgenerate

	// ------------------------------------------------------------------ ID hands over to EX
	// There is no load-use check here: the consumer waits in EX while the load is in MEM
	// (ex_rs*_wait), which costs the same one cycle as waiting in ID (mutation check, ADR 013)
	assign id_advance = id_valid & ~redirect & (~ex_valid_q | ex_advance);
	assign fq_pop     = id_advance;

	// ------------------------------------------------------------------ MEM
	logic [X-1:0] mem_wdata;
	logic [S-1:0] mem_wstrb;
	logic [X-1:0] mem_load_unused;
	logic         mem_misaligned;
	logic         mem_access;
	logic         mem_trap;
	logic         mem_advance;
	logic         wb_can_accept;
	logic         wb_advance;

	lsu u_lsu_mem (
		.funct3_i     (mem_funct3_q),
		.addr_lo_i    (mem_result_q[2:0]),
		.store_data_i (mem_store_q),
		.wdata_o      (mem_wdata),
		.wstrb_o      (mem_wstrb),
		.rdata_i      ('0),
		.load_data_o  (mem_load_unused),
		.misaligned_o (mem_misaligned)
	);

	assign mem_access  = mem_mem_re_q | mem_mem_we_q;
	assign mem_trap    = mem_trap_in_q | (mem_access & mem_misaligned);

	// a misaligned access is a load (4) or store/AMO (6) address misaligned exception: LR counts as a
	// load, SC and the AMOs as stores; mtval is the address
	logic [3:0] mem_cause;
	assign mem_cause = mem_trap_in_q ? mem_cause_q : (mem_mem_we_q ? 4'd6 : 4'd4);

	// the request goes out only when WB can take the instruction in the same cycle, so that the
	// response (one cycle or more later) meets it in WB; a trapping access does not go out
	assign dmem_req_valid_o = mem_valid_q & mem_access & ~mem_trap & wb_can_accept & ~flush;
	assign dmem_req_addr_o  = {mem_result_q[X-1:$clog2(S)], {$clog2(S){1'b0}}};
	assign dmem_req_we_o    = mem_mem_we_q;
	assign dmem_req_wstrb_o = (mem_mem_we_q | mem_amo_q) ? mem_wstrb : '0;
	assign dmem_req_wdata_o = mem_wdata;
	assign dmem_req_amo_o   = mem_amo_q;
	assign dmem_req_atop_o  = mem_sys_q[16:12];

	assign mem_advance    = mem_valid_q & wb_can_accept & (~mem_access | mem_trap | dmem_req_ready_i);
	assign mem_can_accept = ~mem_valid_q | mem_advance;

	// ------------------------------------------------------------------ WB
	logic [X-1:0] wb_load_data;
	logic [X-1:0] wb_wdata_unused;
	logic [S-1:0] wb_wstrb_unused;
	logic         wb_misaligned_unused;
	logic         wb_wait;

	lsu u_lsu_wb (
		.funct3_i     (wb_funct3_q),
		.addr_lo_i    (wb_result_q[2:0]),
		.store_data_i ('0),
		.wdata_o      (wb_wdata_unused),
		.wstrb_o      (wb_wstrb_unused),
		.rdata_i      (dmem_rsp_data_i),
		.load_data_o  (wb_load_data),
		.misaligned_o (wb_misaligned_unused)
	);

	logic unused_lsu;
	assign unused_lsu = &{1'b0, mem_load_unused, wb_wdata_unused, wb_wstrb_unused, wb_misaligned_unused};

	// CSRs (ADR 014): read and written here, in order, by the committing instruction. A CSR that
	// does not exist, or a write to a read-only one, is an illegal instruction found only here
	logic [X-1:0] csr_rdata;
	logic         csr_illegal;
	logic [X-1:0] mtvec, mepc;
	logic         wb_exc;            // WB takes an exception or an interrupt in this cycle
	logic         wb_irq;            // ... an interrupt
	logic         irq_pending, irq_take;
	logic [3:0]   irq_code;
	logic [3:0]   wb_cause;
	logic [X-1:0] wb_tval;

	csr #(.HART_ID(HART_ID)) u_csr (
		.clk_i     (clk_i),
		.rst_ni    (rst_ni),
		.access_i  (wb_valid_q & wb_csr_q),
		.addr_i    (wb_sys_q[16:5]),
		.op_i      (wb_funct3_q[1:0]),
		.write_i   (wb_csr_write_q),
		.wdata_i   (wb_result_q),
		.rdata_o   (csr_rdata),
		.illegal_o (csr_illegal),
		.trap_i    (wb_exc),
		.cause_i   ({wb_irq, (X - 5)'(0), wb_cause}),
		.epc_i     (wb_pc_q),
		.tval_i    (wb_tval),
		.mret_i    (wb_advance & wb_mret_q),
		.mtvec_o   (mtvec),
		.mepc_o    (mepc),
		.irq_i         ({irq_meip_i, irq_mtip_i, irq_msip_i}),
		.irq_pending_o (irq_pending),
		.irq_take_o    (irq_take),
		.irq_code_o    (irq_code),
		.cycle_i   (perf_cycles_o),
		.instret_i (perf_instret_o)
	);

	// an interrupt replaces the WB instruction unless that one has sent its memory request (a trapping
	// access has not: its flags are cleared), or is a WFI
	assign wb_irq   = wb_valid_q & irq_take & ~wb_mem_re_q & ~wb_mem_we_q & ~wb_wfi_q;
	assign wb_exc   = wb_irq | (wb_valid_q & (wb_trap_q | (wb_csr_q & csr_illegal)));
	assign wb_cause = wb_irq ? irq_code : (wb_trap_q ? wb_cause_q : 4'd2);
	// mtval of an illegal CSR access: the instruction, rebuilt from the fields WB has; 0 for an interrupt
	assign wb_tval  = wb_irq ? '0 : (wb_trap_q ? wb_result_q : X'({wb_sys_q, wb_funct3_q, wb_rd_q, 7'b1110011}));

	// loads and stores wait for their response. An exception does not commit: it flushes every
	// younger instruction and fetches from mtvec. MRET and FENCE.I commit and flush, then fetch
	// from mepc, or from pc + 4 (FENCE.I: the instructions after it are fetched again, after the
	// stores before it)
	assign wb_wait       = ((wb_mem_re_q | wb_mem_we_q) & ~wb_trap_q & ~dmem_rsp_valid_i) |
	                       (wb_wfi_q & ~irq_pending);
	assign wb_advance    = wb_valid_q & ~wb_wait & ~wb_exc;
	assign wb_can_accept = ~wb_valid_q | wb_advance;
	assign wb_redirect   = wb_exc | (wb_advance & (wb_mret_q | wb_fence_i_q));
	assign wb_target     = wb_exc ? mtvec : (wb_mret_q ? mepc : wb_result_q);
	assign flush         = wb_redirect;

	assign wb_data = wb_mem_re_q ? wb_load_data : (wb_csr_q ? csr_rdata : wb_result_q);
	assign wb_we   = wb_advance & wb_reg_we_q;
	assign wb_rd   = wb_rd_q;

	// ------------------------------------------------------------------ pipeline registers
	// datapath fields need no reset: a stage is only read while its valid bit is set
	always_ff @(posedge clk_i) begin
		if (!rst_ni) begin
			ex_valid_q  <= 1'b0;
			mem_valid_q <= 1'b0;
			wb_valid_q  <= 1'b0;
			md_busy_q   <= 1'b0;
			md_have_q   <= 1'b0;
		end else if (flush) begin
			// WB redirects: every younger instruction is dropped (the fetch side is in IF). This branch
			// wins over the stage handshakes, so ex_advance and mem_advance need no flush term: an EX
			// redirect in the same cycle is overridden in IF (mutation check, ADR 014). Only the data
			// request must be held back explicitly: it leaves the core in this cycle
			ex_valid_q  <= 1'b0;
			mem_valid_q <= 1'b0;
			wb_valid_q  <= 1'b0;
			md_busy_q   <= 1'b0;
			md_have_q   <= 1'b0;
		end else begin
			// ID -> EX; while EX holds its instruction, the operands follow the newest forwarded
			// values, because the instruction that produced them may leave WB meanwhile
			if (id_advance) begin
				ex_valid_q    <= 1'b1;
				ex_pc_q       <= id_pc;
				ex_inst_q     <= DEBUG ? id_inst : '0;
				ex_alu_op_q   <= id_alu_op;
				ex_word_q     <= id_word;
				ex_a_sel_q    <= id_a_sel;
				ex_b_sel_q    <= id_b_sel;
				ex_wb_sel_q   <= id_wb_sel;
				ex_reg_we_q   <= id_reg_we;
				ex_mem_re_q   <= id_mem_re;
				ex_mem_we_q   <= id_mem_we;
				ex_branch_q   <= id_branch;
				ex_jal_q      <= id_jal;
				ex_jalr_q     <= id_jalr;
				ex_muldiv_q   <= id_muldiv;
				ex_illegal_q  <= id_illegal;
				ex_csr_q      <= id_csr;
				ex_csr_write_q <= id_csr_write;
				ex_ecall_q    <= id_ecall;
				ex_ebreak_q   <= id_ebreak;
				ex_mret_q     <= id_mret;
				ex_wfi_q      <= id_wfi;
				ex_fence_i_q  <= id_fence_i;
				ex_amo_q      <= id_amo;
				ex_funct3_q   <= id_funct3;
				ex_rd_q       <= id_inst[11:7];
				ex_rs1_q      <= id_inst[19:15];
				ex_rs2_q      <= id_inst[24:20];
				ex_use_rs1_q  <= id_use_rs1;
				ex_use_rs2_q  <= id_use_rs2;
				ex_rs1_data_q <= id_rs1_data;
				ex_rs2_data_q <= id_rs2_data;
				// an illegal, CSR or atomic instruction carries its own bits instead of the immediate:
				// mtval, the CSR address, the 5-bit immediate and the AMO operation come from them
				ex_imm_q      <= (id_illegal | id_csr | id_amo) ? X'(id_inst) : id_imm;
				ex_imm4_q     <= id_imm4;
				ex_pred_q     <= fq_pred_q[0];
				ex_ptgt_q     <= fq_ptgt_q[0];
			end else begin
				if (ex_advance) ex_valid_q <= 1'b0;
				if (!ex_rs1_wait) ex_rs1_data_q <= ex_rs1;
				if (!ex_rs2_wait) ex_rs2_data_q <= ex_rs2;
			end

			// multiplier/divider state of the EX instruction
			if (ex_advance) begin
				md_busy_q <= 1'b0;
				md_have_q <= 1'b0;
			end else begin
				if (md_valid)            md_busy_q <= 1'b1;
				if (md_done && md_busy_q) begin
					md_busy_q <= 1'b0;
					md_have_q <= 1'b1;
					md_res_q  <= md_result;
				end
			end

			// EX -> MEM
			if (ex_advance) begin
				mem_valid_q   <= 1'b1;
				mem_pc_q      <= ex_pc_q;
				mem_inst_q    <= DEBUG ? ex_inst_q : '0;
				mem_reg_we_q  <= ex_reg_we_q & ~ex_trap;
				mem_mem_re_q  <= ex_mem_re_q;
				mem_mem_we_q  <= ex_mem_we_q;
				mem_trap_in_q <= ex_trap;
				mem_cause_q   <= ex_cause;
				mem_csr_q     <= ex_csr_q & ~ex_trap;
				mem_csr_write_q <= ex_csr_write_q;
				mem_mret_q    <= ex_mret_q & ~ex_trap;
				mem_wfi_q     <= ex_wfi_q & ~ex_trap;
				mem_fence_i_q <= ex_fence_i_q & ~ex_trap;
				mem_amo_q     <= ex_amo_q & ~ex_trap;
				mem_sys_q     <= ex_imm_q[31:15];
				mem_funct3_q  <= ex_funct3_q;
				mem_rd_q      <= ex_rd_q;
				mem_result_q  <= ex_result;
				mem_store_q   <= ex_rs2;
			end else if (mem_advance) begin
				mem_valid_q <= 1'b0;
			end

			// MEM -> WB
			if (mem_advance) begin
				wb_valid_q  <= 1'b1;
				wb_pc_q     <= mem_pc_q;
				wb_inst_q   <= DEBUG ? mem_inst_q : '0;
				wb_reg_we_q <= mem_reg_we_q & ~mem_trap;
				wb_mem_re_q <= mem_mem_re_q & ~mem_trap;
				wb_mem_we_q <= mem_mem_we_q & ~mem_trap;
				wb_trap_q   <= mem_trap;
				wb_cause_q  <= mem_cause;
				wb_csr_q    <= mem_csr_q & ~mem_trap;
				wb_csr_write_q <= mem_csr_write_q;
				wb_mret_q   <= mem_mret_q & ~mem_trap;
				wb_wfi_q    <= mem_wfi_q & ~mem_trap;
				wb_fence_i_q <= mem_fence_i_q & ~mem_trap;
				wb_sys_q    <= mem_sys_q;
				wb_funct3_q <= mem_funct3_q;
				wb_rd_q     <= mem_rd_q;
				wb_result_q <= mem_result_q;
				wb_wdata_q  <= DEBUG ? mem_wdata : '0;
				wb_wstrb_q  <= (DEBUG && mem_mem_we_q) ? mem_wstrb : '0;
			end else if (wb_advance) begin
				wb_valid_q <= 1'b0;
			end
		end
	end

	logic unused_ex;
	assign unused_ex = &{1'b0, ex_wb_sel_q};

	// ------------------------------------------------------------------ outputs
	// an exception is taken in this cycle (commit_pc_o shows the trapping instruction)
	assign trap_o = wb_exc;

	// the commit trace exists for verification only (DEBUG = 0: all zero)
	assign commit_valid_o     = DEBUG & wb_advance;
	assign commit_pc_o        = DEBUG ? wb_pc_q : '0;
	assign commit_inst_o      = wb_inst_q;
	assign commit_we_o        = DEBUG & wb_we & (wb_rd_q != 5'd0);
	assign commit_rd_o        = DEBUG ? wb_rd_q : '0;
	assign commit_data_o      = DEBUG ? wb_data : '0;
	assign commit_mem_re_o    = DEBUG & wb_mem_re_q;
	assign commit_mem_we_o    = DEBUG & wb_mem_we_q;
	assign commit_mem_addr_o  = DEBUG ? wb_result_q : '0;
	assign commit_mem_wdata_o = wb_wdata_q;
	assign commit_mem_wstrb_o = wb_wstrb_q;

	perf_counters u_perf_counters (
		.clk_i     (clk_i),
		.rst_ni    (rst_ni),
		.commit_i  (wb_advance),
		.stall_i   (DEBUG & id_valid & ~id_advance & ~redirect),
		.flush_i   (DEBUG & redirect),
		.cycles_o  (perf_cycles_o),
		.instret_o (perf_instret_o),
		.stalls_o  (perf_stalls_o),
		.flushes_o (perf_flushes_o)
	);

endmodule : core_pipe
