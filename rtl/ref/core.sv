// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

module core #(
	parameter logic [riscv_pkg::XLEN-1:0] RESET_PC = riscv_pkg::XLEN'(64'h8000_0000)
) (
	input  logic                          clk_i,
	input  logic                          rst_ni,

	// instruction memory: combinational read (single-cycle core, ADR 008)
	output logic [riscv_pkg::XLEN-1:0]    imem_addr_o,
	input  logic [31:0]                   imem_rdata_i,

	// data memory: XLEN bits wide, combinational read, write on the clock edge
	output logic [riscv_pkg::XLEN-1:0]    dmem_addr_o,
	output logic                          dmem_re_o,
	output logic [riscv_pkg::XLEN-1:0]    dmem_wdata_o,
	output logic [riscv_pkg::XLEN/8-1:0]  dmem_wstrb_o,
	input  logic [riscv_pkg::XLEN-1:0]    dmem_rdata_i,

	// the core stops on an illegal instruction, a misaligned access or a misaligned jump target
	output logic                          trap_o,

	// commit trace: one instruction per cycle while commit_valid_o is 1
	output logic                          commit_valid_o,
	output logic [riscv_pkg::XLEN-1:0]    commit_pc_o,
	output logic [31:0]                   commit_inst_o,
	output logic                          commit_we_o,
	output logic [4:0]                    commit_rd_o,
	output logic [riscv_pkg::XLEN-1:0]    commit_data_o,

	// performance counters: cycles, retired instructions, stalls, flushes (taken branches and jumps)
	output logic [63:0]                   perf_cycles_o,
	output logic [63:0]                   perf_instret_o,
	output logic [63:0]                   perf_stalls_o,
	output logic [63:0]                   perf_flushes_o
);

	localparam int X = riscv_pkg::XLEN;

	// fetch
	logic [X-1:0] pc_q;
	logic [X-1:0] pc_d;
	logic [X-1:0] pc_plus4;
	logic [31:0]  inst;

	// decoder
	riscv_pkg::alu_op_t  alu_op;
	logic                word;
	riscv_pkg::imm_sel_t imm_sel;
	riscv_pkg::a_sel_t   a_sel;
	riscv_pkg::b_sel_t   b_sel;
	riscv_pkg::wb_sel_t  wb_sel;
	logic                reg_we;
	logic                mem_re;
	logic                mem_we;
	logic                branch;
	logic                jal;
	logic                jalr;
	logic                muldiv;
	logic                sys_csr, sys_ecall, sys_ebreak, sys_mret, sys_fence_i, amo;
	logic                sys_wfi;            // a no-op here: the reference core has no interrupts
	logic                unused_wfi;

	assign unused_wfi = sys_wfi;
	logic [2:0]          funct3;
	logic                illegal;

	// datapath
	logic [4:0]   rd;
	logic [X-1:0] rs1_data;
	logic [X-1:0] rs2_data;
	logic [X-1:0] imm;
	logic [X-1:0] alu_a;
	logic [X-1:0] alu_b;
	logic [X-1:0] alu_r;
	logic [X-1:0] wb_data;
	logic         taken;
	logic         reg_we_eff;
	logic         fetch_misaligned;

	// M extension: the multiplier/divider takes several cycles, the core waits on the same instruction
	logic         md_done;
	logic [X-1:0] md_result;
	logic         stall;

	// load/store
	logic [X-1:0]   lsu_wdata;
	logic [X/8-1:0] lsu_wstrb;
	logic [X-1:0]   load_data;
	logic           misaligned;

	assign inst        = imem_rdata_i;
	assign imem_addr_o = pc_q;
	assign pc_plus4    = pc_q + X'(4);
	assign rd          = inst[11:7];

	decoder u_decoder (
		.inst_i    (inst),
		.alu_op_o  (alu_op),
		.word_o    (word),
		.imm_sel_o (imm_sel),
		.a_sel_o   (a_sel),
		.b_sel_o   (b_sel),
		.wb_sel_o  (wb_sel),
		.reg_we_o  (reg_we),
		.mem_re_o  (mem_re),
		.mem_we_o  (mem_we),
		.branch_o  (branch),
		.jal_o     (jal),
		.jalr_o    (jalr),
		.muldiv_o  (muldiv),
		.csr_o     (sys_csr),
		.ecall_o   (sys_ecall),
		.ebreak_o  (sys_ebreak),
		.mret_o    (sys_mret),
		.wfi_o     (sys_wfi),
		.fence_i_o (sys_fence_i),
		.amo_o     (amo),
		.funct3_o  (funct3),
		.illegal_o (illegal)
	);

	// a jump or taken branch to an address that is not 4-byte aligned (no C extension) is an
	// instruction-address-misaligned exception on the jump itself: rd is not written. Bit 0 of the
	// target is never set (JALR clears it, the branch and jump offsets are even), so bit 1 decides
	assign fetch_misaligned = (jal | jalr | (branch & taken)) & alu_r[1];

	// a trapping instruction changes nothing: no register write, no store, pc stays. The reference
	// core has no CSRs and no exception entry (the pipelined core has, ADR 014): CSR instructions,
	// ECALL, EBREAK, MRET and FENCE.I stop it like an illegal instruction, and so do the atomics
	// of the A extension (they are executed by the memory side of the pipelined core, ADR 019)
	assign trap_o     = illegal | (misaligned & (mem_re | mem_we)) | fetch_misaligned |
	                    sys_csr | sys_ecall | sys_ebreak | sys_mret | sys_fence_i | amo;

	// a waiting instruction changes nothing either: it is committed in the cycle when the unit is done
	assign stall      = muldiv & ~md_done;
	assign reg_we_eff = reg_we & ~trap_o & ~stall;

	// single-cycle core: the write belongs to the instruction that reads, so reads must see the old value
	regfile #(.WRITE_THROUGH(1'b0)) u_regfile (
		.clk_i    (clk_i),
		.we_i     (reg_we_eff),
		.waddr_i  (rd),
		.wdata_i  (wb_data),
		.raddr1_i (inst[19:15]),
		.raddr2_i (inst[24:20]),
		.rdata1_o (rs1_data),
		.rdata2_o (rs2_data)
	);

	imm_gen u_imm_gen (
		.inst_i (inst),
		.sel_i  (imm_sel),
		.imm_o  (imm)
	);

	always_comb begin
		alu_a = rs1_data;
		alu_b = rs2_data;

		case (a_sel)
			riscv_pkg::A_PC:   alu_a = pc_q;
			riscv_pkg::A_ZERO: alu_a = '0;
			default:           alu_a = rs1_data;
		endcase // a_sel

		if (b_sel == riscv_pkg::B_IMM) alu_b = imm;
	end

	alu u_alu (
		.a_i    (alu_a),
		.b_i    (alu_b),
		.op_i   (alu_op),
		.word_i (word),
		.r_o    (alu_r)
	);

	branch_unit u_branch_unit (
		.a_i      (rs1_data),
		.b_i      (rs2_data),
		.funct3_i (funct3),
		.taken_o  (taken)
	);

	muldiv u_muldiv (
		.clk_i    (clk_i),
		.rst_ni   (rst_ni),
		.valid_i  (muldiv),
		.abort_i  (1'b0),
		.funct3_i (funct3),
		.word_i   (word),
		.a_i      (rs1_data),
		.b_i      (rs2_data),
		.done_o   (md_done),
		.result_o (md_result)
	);

	lsu u_lsu (
		.funct3_i     (funct3),
		.addr_lo_i    (alu_r[2:0]),
		.store_data_i (rs2_data),
		.wdata_o      (lsu_wdata),
		.wstrb_o      (lsu_wstrb),
		.rdata_i      (dmem_rdata_i),
		.load_data_o  (load_data),
		.misaligned_o (misaligned)
	);

	assign dmem_addr_o  = alu_r;
	assign dmem_re_o    = mem_re & ~trap_o;
	assign dmem_wdata_o = lsu_wdata;
	assign dmem_wstrb_o = (mem_we & ~trap_o) ? lsu_wstrb : '0;

	always_comb begin
		wb_data = alu_r;

		case (wb_sel)
			riscv_pkg::WB_MEM: wb_data = load_data;
			riscv_pkg::WB_PC4: wb_data = pc_plus4;
			riscv_pkg::WB_MD:  wb_data = md_result;
			default:           wb_data = alu_r;
		endcase // wb_sel
	end

	// next pc: JAL and taken branches go to pc + imm (the ALU result), JALR to rs1 + imm with bit 0 cleared
	always_comb begin
		pc_d = pc_plus4;

		if (trap_o || stall)               pc_d = pc_q;
		else if (jal || (branch && taken)) pc_d = alu_r;
		else if (jalr)                 pc_d = {alu_r[X-1:1], 1'b0};
	end

	always_ff @(posedge clk_i) begin
		if (!rst_ni) pc_q <= RESET_PC;
		else         pc_q <= pc_d;
	end

	// commit trace: writes to x0 are not reported
	assign commit_valid_o = rst_ni & ~trap_o & ~stall;
	assign commit_pc_o    = pc_q;
	assign commit_inst_o  = inst;
	assign commit_we_o    = reg_we_eff & (rd != 5'd0);
	assign commit_rd_o    = rd;
	assign commit_data_o  = wb_data;

	// the only stall of the single-cycle core is waiting for the multiplier/divider;
	// a redirect is the event that flushes the pipeline later
	perf_counters u_perf_counters (
		.clk_i     (clk_i),
		.rst_ni    (rst_ni),
		.commit_i  (commit_valid_o),
		.stall_i   (rst_ni & stall),
		.flush_i   (commit_valid_o & (jal | jalr | (branch & taken))),
		.cycles_o  (perf_cycles_o),
		.instret_o (perf_instret_o),
		.stalls_o  (perf_stalls_o),
		.flushes_o (perf_flushes_o)
	);

endmodule : core
