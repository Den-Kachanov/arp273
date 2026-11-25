// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

// Five-stage pipelined core, RV64IM (RV32IM for XLEN = 32), ADR 013
//
//   IF   fetch: requests to the instruction port, responses into a 2-entry fetch queue
//   ID   decode, register read (the head of the fetch queue is the ID instruction)
//   EX   ALU, branch decision, jump targets, forwarding, multiplier/divider
//   MEM  request to the data port (address, store data and strobes)
//   WB   load response, register write, commit
//
// Memory ports (ADR 013): valid/ready request, response at least one cycle later, in order,
// one outstanding request per port, a response for every request (stores too)
//
// Hazards:
//   forwarding from MEM (not loads) and from WB into EX, never for x0
//   load-use: the consumer waits in EX one cycle while the load is in MEM (its data arrives in WB)
//   taken branches and jumps are decided in EX: ID and the fetch queue are flushed and a
//   response still on the way is dropped
//   the multiplier/divider holds EX until done; the result is kept if MEM cannot take it yet
//   a trap reaches WB and freezes the pipeline: younger instructions write nothing

module core_pipe #(
	parameter logic [riscv_pkg::XLEN-1:0] RESET_PC = riscv_pkg::XLEN'(64'h8000_0000)
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
	input  logic                          dmem_rsp_valid_i,
	input  logic [riscv_pkg::XLEN-1:0]    dmem_rsp_data_i,

	// a trap in WB stops the core; commit_pc_o shows the trapping instruction
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

	logic         redirect;          // from EX
	logic [X-1:0] redirect_pc;
	logic [X-1:0] redirect_pc4;      // redirect_pc + 4, computed in parallel (see EX)
	logic         fq_push;
	logic         fq_pop;            // ID hands its instruction to EX
	logic [1:0]   fq_occ_next;       // occupancy after this cycle, before a new request
	logic         fetch_issue;
	logic         fetch_accept;
	logic [X-1:0] fetch_addr;

	assign fq_push      = imem_rsp_valid_i & out_q & ~drop_q & ~redirect;
	assign fq_occ_next  = redirect ? 2'd0 : (fq_cnt_q + {1'b0, fq_push} - {1'b0, fq_pop});
	// one outstanding request; a new one may start in the cycle the previous response arrives,
	// and only if the queue will have room for its response. On a redirect the target is fetched
	// in the same cycle when the port allows it: a taken branch then costs two cycles, not three
	assign fetch_addr   = redirect ? redirect_pc : pc_f_q;
	assign fetch_issue  = rst_ni & (~out_q | imem_rsp_valid_i) & (fq_occ_next <= 2'd1);
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
			if (fetch_accept)  pc_f_q <= redirect ? redirect_pc4 : pc_f_q + X'(4);
			else if (redirect) pc_f_q <= redirect_pc;

			out_q    <= fetch_accept | (out_q & ~imem_rsp_valid_i);
			// a response still on the way when the path changes is dropped when it arrives (a request
			// on the new path can only start once it has arrived: one outstanding request)
			drop_q   <= redirect ? (out_q & ~imem_rsp_valid_i) : (drop_q & ~imem_rsp_valid_i);
			fq_cnt_q <= fq_occ_next;

			// queue contents: pop shifts entry 1 down, push writes the first free entry
			if (fq_pop) begin
				fq_pc_q[0]   <= fq_pc_q[1];
				fq_inst_q[0] <= fq_inst_q[1];
			end
			if (fq_push) begin
				if (fq_cnt_q - {1'b0, fq_pop} == 2'd0) begin
					fq_pc_q[0]   <= out_pc_q;
					fq_inst_q[0] <= imem_rsp_data_i;
				end else begin
					fq_pc_q[1]   <= out_pc_q;
					fq_inst_q[1] <= imem_rsp_data_i;
				end
			end
		end
		if (fetch_accept) out_pc_q <= fetch_addr;
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
	logic [2:0]          ex_funct3_q;
	logic [4:0]          ex_rd_q, ex_rs1_q, ex_rs2_q;
	logic                ex_use_rs1_q, ex_use_rs2_q;
	logic [X-1:0]        ex_rs1_data_q, ex_rs2_data_q, ex_imm_q, ex_imm4_q;

	// ------------------------------------------------------------------ EX/MEM and MEM/WB registers
	logic                mem_valid_q;
	logic [X-1:0]        mem_pc_q;
	logic [31:0]         mem_inst_q;
	logic                mem_reg_we_q, mem_mem_re_q, mem_mem_we_q, mem_trap_in_q;
	logic [2:0]          mem_funct3_q;
	logic [4:0]          mem_rd_q;
	logic [X-1:0]        mem_result_q;     // ALU result, link address or M result; the address for loads and stores
	logic [X-1:0]        mem_store_q;

	logic                wb_valid_q;
	logic [X-1:0]        wb_pc_q;
	logic [31:0]         wb_inst_q;
	logic                wb_reg_we_q, wb_mem_re_q, wb_mem_we_q, wb_trap_q;
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
	logic [X-1:0] ex_result;
	logic         ex_ops_ready;
	logic         ex_done;
	logic         ex_advance;
	logic         mem_can_accept;

	// sources: MEM (not a load: its data is not there yet), then WB, then the ID/EX register
	assign mem_fwd_ok      = mem_valid_q & mem_reg_we_q & ~mem_mem_re_q & (mem_rd_q != 5'd0);
	assign wb_fwd_ok       = wb_valid_q & wb_reg_we_q & (wb_rd_q != 5'd0);
	assign wb_load_pending = wb_valid_q & wb_mem_re_q & ~dmem_rsp_valid_i;

	always_comb begin
		ex_rs1      = ex_rs1_data_q;
		ex_rs2      = ex_rs2_data_q;
		ex_rs1_wait = 1'b0;
		ex_rs2_wait = 1'b0;

		if (mem_fwd_ok && mem_rd_q == ex_rs1_q)                       ex_rs1 = mem_result_q;
		else if (mem_valid_q && mem_mem_re_q && mem_reg_we_q && mem_rd_q != 5'd0 && mem_rd_q == ex_rs1_q)
		                                                              ex_rs1_wait = 1'b1;
		else if (wb_fwd_ok && wb_rd_q == ex_rs1_q) begin
			ex_rs1      = wb_data;
			ex_rs1_wait = wb_load_pending;
		end

		if (mem_fwd_ok && mem_rd_q == ex_rs2_q)                       ex_rs2 = mem_result_q;
		else if (mem_valid_q && mem_mem_re_q && mem_reg_we_q && mem_rd_q != 5'd0 && mem_rd_q == ex_rs2_q)
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
	assign ex_trap             = ex_illegal_q | ex_fetch_misaligned;

	// multiplier/divider: valid is held from the start until done; the result is kept in md_res_q
	// when MEM cannot take it in the done cycle
	logic         md_valid, md_done, md_busy_q, md_have_q;
	logic [X-1:0] md_result, md_res_q;

	assign md_valid = ex_valid_q & ex_muldiv_q & ~md_have_q & (md_busy_q | ex_ops_ready);

	muldiv u_muldiv (
		.clk_i    (clk_i),
		.rst_ni   (rst_ni),
		.valid_i  (md_valid),
		.funct3_i (ex_funct3_q),
		.word_i   (ex_word_q),
		.a_i      (ex_rs1),
		.b_i      (ex_rs2),
		.done_o   (md_done),
		.result_o (md_result)
	);

	always_comb begin
		ex_result = alu_r;
		if (ex_jal_q || ex_jalr_q) ex_result = ex_pc_q + X'(4);
		else if (ex_muldiv_q)      ex_result = md_have_q ? md_res_q : md_result;
	end

	assign ex_done    = ex_ops_ready & (~ex_muldiv_q | md_have_q | md_done);
	assign ex_advance = ex_valid_q & ex_done & mem_can_accept;

	assign redirect    = ex_advance & ex_ctrl_taken & ~ex_trap;
	assign redirect_pc = ex_jalr_q ? {alu_r[X-1:1], 1'b0} : alu_r;

	// target + 4 = base + (imm + 4), in parallel with the ALU; for JALR bit 0 is cleared after the
	// add, which gives the same result because 4 does not touch bit 0
	logic [X-1:0] tgt4;
	assign tgt4         = (ex_jalr_q ? ex_rs1 : ex_pc_q) + ex_imm4_q;
	assign redirect_pc4 = ex_jalr_q ? {tgt4[X-1:1], 1'b0} : tgt4;

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

	// the request goes out only when WB can take the instruction in the same cycle, so that the
	// response (one cycle or more later) meets it in WB; a trapping access does not go out
	assign dmem_req_valid_o = mem_valid_q & mem_access & ~mem_trap & wb_can_accept;
	assign dmem_req_addr_o  = {mem_result_q[X-1:$clog2(S)], {$clog2(S){1'b0}}};
	assign dmem_req_we_o    = mem_mem_we_q;
	assign dmem_req_wstrb_o = mem_mem_we_q ? mem_wstrb : '0;
	assign dmem_req_wdata_o = mem_wdata;

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

	// loads and stores wait for their response; a trap freezes WB (and so the whole pipeline)
	assign wb_wait       = (wb_mem_re_q | wb_mem_we_q) & ~wb_trap_q & ~dmem_rsp_valid_i;
	assign wb_advance    = wb_valid_q & ~wb_wait & ~wb_trap_q;
	assign wb_can_accept = ~wb_valid_q | wb_advance;

	assign wb_data = wb_mem_re_q ? wb_load_data : wb_result_q;
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
		end else begin
			// ID -> EX; while EX holds its instruction, the operands follow the newest forwarded
			// values, because the instruction that produced them may leave WB meanwhile
			if (id_advance) begin
				ex_valid_q    <= 1'b1;
				ex_pc_q       <= id_pc;
				ex_inst_q     <= id_inst;
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
				ex_funct3_q   <= id_funct3;
				ex_rd_q       <= id_inst[11:7];
				ex_rs1_q      <= id_inst[19:15];
				ex_rs2_q      <= id_inst[24:20];
				ex_use_rs1_q  <= id_use_rs1;
				ex_use_rs2_q  <= id_use_rs2;
				ex_rs1_data_q <= id_rs1_data;
				ex_rs2_data_q <= id_rs2_data;
				ex_imm_q      <= id_imm;
				ex_imm4_q     <= id_imm4;
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
				mem_inst_q    <= ex_inst_q;
				mem_reg_we_q  <= ex_reg_we_q & ~ex_trap;
				mem_mem_re_q  <= ex_mem_re_q;
				mem_mem_we_q  <= ex_mem_we_q;
				mem_trap_in_q <= ex_trap;
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
				wb_inst_q   <= mem_inst_q;
				wb_reg_we_q <= mem_reg_we_q & ~mem_trap;
				wb_mem_re_q <= mem_mem_re_q & ~mem_trap;
				wb_mem_we_q <= mem_mem_we_q & ~mem_trap;
				wb_trap_q   <= mem_trap;
				wb_funct3_q <= mem_funct3_q;
				wb_rd_q     <= mem_rd_q;
				wb_result_q <= mem_result_q;
				wb_wdata_q  <= mem_wdata;
				wb_wstrb_q  <= mem_mem_we_q ? mem_wstrb : '0;
			end else if (wb_advance) begin
				wb_valid_q <= 1'b0;
			end
		end
	end

	logic unused_ex;
	assign unused_ex = &{1'b0, ex_wb_sel_q};

	// ------------------------------------------------------------------ outputs
	assign trap_o = wb_valid_q & wb_trap_q;

	assign commit_valid_o     = wb_advance;
	assign commit_pc_o        = wb_pc_q;
	assign commit_inst_o      = wb_inst_q;
	assign commit_we_o        = wb_we & (wb_rd_q != 5'd0);
	assign commit_rd_o        = wb_rd_q;
	assign commit_data_o      = wb_data;
	assign commit_mem_re_o    = wb_mem_re_q;
	assign commit_mem_we_o    = wb_mem_we_q;
	assign commit_mem_addr_o  = wb_result_q;
	assign commit_mem_wdata_o = wb_wdata_q;
	assign commit_mem_wstrb_o = wb_wstrb_q;

	perf_counters u_perf_counters (
		.clk_i     (clk_i),
		.rst_ni    (rst_ni),
		.commit_i  (commit_valid_o),
		.stall_i   (id_valid & ~id_advance & ~redirect),
		.flush_i   (redirect),
		.cycles_o  (perf_cycles_o),
		.instret_o (perf_instret_o),
		.stalls_o  (perf_stalls_o),
		.flushes_o (perf_flushes_o)
	);

endmodule : core_pipe
