// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

// The SoC, stage 2 step 3 (ADR 019): NHARTS Sol cores on one memory, without caches yet.
//
//   cores      core_pipe with HART_ID = 0 .. NHARTS-1, all starting at RESET_PC; software tells them
//              apart by mhartid (sw/crt0.S)
//   fetch      the instruction ports of all cores share one instruction port (rr_arbiter)
//   data       the data ports share one data port (rr_arbiter); then the address decides:
//                0x0200_0000 .. 0x0200_FFFF  the CLINT (rtl/soc/clint.sv): timers and software interrupts
//                anything else               the atomics unit (rtl/soc/amo_unit.sv), then mem_d_* out
//              The memory outside (RAM, UART, tohost in simulation) sees plain loads and stores
//   interrupts MSIP and MTIP of every hart from the CLINT; MEIP from outside (the PLIC comes with the bus)
//
// Each shared port has one request in flight: two cores share the bandwidth of one memory. This is the
// correctness step; the L1 caches of step 4 give each core its own path again.
//
// The commit trace and the counters of every hart come out side by side: hart h in bits [h*W +: W]

module soc_top #(
	parameter int NHARTS   = 2,
	parameter logic [riscv_pkg::XLEN-1:0] RESET_PC = riscv_pkg::XLEN'(64'h8000_0000),
	parameter bit DEBUG    = 1'b1,
	parameter int MUL_BITS = 4,
	parameter bit BP       = 1'b0
) (
	input  logic                                 clk_i,
	input  logic                                 rst_ni,
	input  logic                                 tick_i,         // mtime increment

	// instruction memory
	output logic                                 mem_i_req_valid_o,
	input  logic                                 mem_i_req_ready_i,
	output logic [riscv_pkg::XLEN-1:0]           mem_i_req_addr_o,
	input  logic                                 mem_i_rsp_valid_i,
	input  logic [31:0]                          mem_i_rsp_data_i,

	// data memory and the devices outside: plain loads and stores
	output logic                                 mem_d_req_valid_o,
	input  logic                                 mem_d_req_ready_i,
	output logic [riscv_pkg::XLEN-1:0]           mem_d_req_addr_o,
	output logic                                 mem_d_req_we_o,
	output logic [riscv_pkg::XLEN/8-1:0]         mem_d_req_wstrb_o,
	output logic [riscv_pkg::XLEN-1:0]           mem_d_req_wdata_o,
	input  logic                                 mem_d_rsp_valid_i,
	input  logic [riscv_pkg::XLEN-1:0]           mem_d_rsp_data_i,

	input  logic [NHARTS-1:0]                    irq_meip_i,

	// per hart
	output logic [NHARTS-1:0]                    trap_o,
	output logic [NHARTS-1:0]                    commit_valid_o,
	output logic [NHARTS*riscv_pkg::XLEN-1:0]    commit_pc_o,
	output logic [NHARTS*32-1:0]                 commit_inst_o,
	output logic [NHARTS-1:0]                    commit_we_o,
	output logic [NHARTS*5-1:0]                  commit_rd_o,
	output logic [NHARTS*riscv_pkg::XLEN-1:0]    commit_data_o,
	output logic [NHARTS-1:0]                    commit_mem_re_o,
	output logic [NHARTS-1:0]                    commit_mem_we_o,
	output logic [NHARTS*riscv_pkg::XLEN-1:0]    commit_mem_addr_o,
	output logic [NHARTS*riscv_pkg::XLEN-1:0]    commit_mem_wdata_o,
	output logic [NHARTS*riscv_pkg::XLEN/8-1:0]  commit_mem_wstrb_o,
	output logic [NHARTS*64-1:0]                 perf_cycles_o,
	output logic [NHARTS*64-1:0]                 perf_instret_o
);

	localparam int X  = riscv_pkg::XLEN;
	localparam int S  = X / 8;
	localparam int IW = (NHARTS > 1) ? $clog2(NHARTS) : 1;
	localparam int DW = X + 1 + S + X + 1 + 5;            // data request: addr, we, wstrb, wdata, amo, atop
	localparam logic [X-1:0] CLINT_BASE = X'(32'h0200_0000);

	// ------------------------------------------------------------------ cores
	logic [NHARTS-1:0]   i_req_valid, i_req_ready, i_rsp_valid;
	logic [NHARTS*X-1:0] i_req_addr;
	logic [31:0]         i_rsp_data;
	logic [NHARTS-1:0]   d_req_valid, d_req_ready, d_rsp_valid;
	logic [NHARTS*DW-1:0] d_req;
	logic [X-1:0]        d_rsp_data;
	logic [NHARTS-1:0]   msip, mtip;

	for (genvar h = 0; h < NHARTS; h++) begin : g_hart
		logic [X-1:0]   d_addr, d_wdata;
		logic           d_we, d_amo;
		logic [S-1:0]   d_wstrb;
		logic [4:0]     d_atop;
		logic [63:0]    stalls, flushes;
		logic           unused_perf;

		core_pipe #(
			.RESET_PC (RESET_PC),
			.HART_ID  (h),
			.DEBUG    (DEBUG),
			.MUL_BITS (MUL_BITS),
			.BP       (BP)
		) u_core (
			.clk_i              (clk_i),
			.rst_ni             (rst_ni),
			.imem_req_valid_o   (i_req_valid[h]),
			.imem_req_ready_i   (i_req_ready[h]),
			.imem_req_addr_o    (i_req_addr[h*X +: X]),
			.imem_rsp_valid_i   (i_rsp_valid[h]),
			.imem_rsp_data_i    (i_rsp_data),
			.dmem_req_valid_o   (d_req_valid[h]),
			.dmem_req_ready_i   (d_req_ready[h]),
			.dmem_req_addr_o    (d_addr),
			.dmem_req_we_o      (d_we),
			.dmem_req_wstrb_o   (d_wstrb),
			.dmem_req_wdata_o   (d_wdata),
			.dmem_req_amo_o     (d_amo),
			.dmem_req_atop_o    (d_atop),
			.dmem_rsp_valid_i   (d_rsp_valid[h]),
			.dmem_rsp_data_i    (d_rsp_data),
			.irq_msip_i         (msip[h]),
			.irq_mtip_i         (mtip[h]),
			.irq_meip_i         (irq_meip_i[h]),
			.trap_o             (trap_o[h]),
			.commit_valid_o     (commit_valid_o[h]),
			.commit_pc_o        (commit_pc_o[h*X +: X]),
			.commit_inst_o      (commit_inst_o[h*32 +: 32]),
			.commit_we_o        (commit_we_o[h]),
			.commit_rd_o        (commit_rd_o[h*5 +: 5]),
			.commit_data_o      (commit_data_o[h*X +: X]),
			.commit_mem_re_o    (commit_mem_re_o[h]),
			.commit_mem_we_o    (commit_mem_we_o[h]),
			.commit_mem_addr_o  (commit_mem_addr_o[h*X +: X]),
			.commit_mem_wdata_o (commit_mem_wdata_o[h*X +: X]),
			.commit_mem_wstrb_o (commit_mem_wstrb_o[h*S +: S]),
			.perf_cycles_o      (perf_cycles_o[h*64 +: 64]),
			.perf_instret_o     (perf_instret_o[h*64 +: 64]),
			.perf_stalls_o      (stalls),
			.perf_flushes_o     (flushes)
		);

		assign d_req[h*DW +: DW] = {d_addr, d_we, d_wstrb, d_wdata, d_amo, d_atop};
		assign unused_perf = &{1'b0, stalls, flushes};
	end

	// ------------------------------------------------------------------ fetch
	logic [IW-1:0] i_id_unused;

	rr_arbiter #(.N(NHARTS), .W(X), .RW(32)) u_arb_i (
		.clk_i       (clk_i),
		.rst_ni      (rst_ni),
		.req_valid_i (i_req_valid),
		.req_ready_o (i_req_ready),
		.req_data_i  (i_req_addr),
		.rsp_valid_o (i_rsp_valid),
		.rsp_data_o  (i_rsp_data),
		.req_valid_o (mem_i_req_valid_o),
		.req_ready_i (mem_i_req_ready_i),
		.req_data_o  (mem_i_req_addr_o),
		.req_id_o    (i_id_unused),
		.rsp_valid_i (mem_i_rsp_valid_i),
		.rsp_data_i  (mem_i_rsp_data_i)
	);

	// ------------------------------------------------------------------ data: arbiter, decode
	logic          d_valid, d_ready, d_rsp_v;
	logic [DW-1:0] d_bus;
	logic [IW-1:0] d_id;
	logic [X-1:0]  d_rsp_d;
	logic [X-1:0]  a_addr, a_wdata;
	logic          a_we, a_amo;
	logic [S-1:0]  a_wstrb;
	logic [4:0]    a_atop;

	rr_arbiter #(.N(NHARTS), .W(DW), .RW(X)) u_arb_d (
		.clk_i       (clk_i),
		.rst_ni      (rst_ni),
		.req_valid_i (d_req_valid),
		.req_ready_o (d_req_ready),
		.req_data_i  (d_req),
		.rsp_valid_o (d_rsp_valid),
		.rsp_data_o  (d_rsp_data),
		.req_valid_o (d_valid),
		.req_ready_i (d_ready),
		.req_data_o  (d_bus),
		.req_id_o    (d_id),
		.rsp_valid_i (d_rsp_v),
		.rsp_data_i  (d_rsp_d)
	);

	assign {a_addr, a_we, a_wstrb, a_wdata, a_amo, a_atop} = d_bus;

	// the target of the request now, and of the one in flight (for its response)
	logic to_clint, clint_q;
	assign to_clint = (a_addr[X-1:16] == CLINT_BASE[X-1:16]);

	always_ff @(posedge clk_i) begin
		if (!rst_ni)                clint_q <= 1'b0;
		else if (d_valid && d_ready) clint_q <= to_clint;
	end

	// the CLINT (an atomic sent there acts as a plain access: atomics are for RAM)
	logic         c_ready, c_rsp_v;
	logic [X-1:0] c_rsp_d;

	clint #(.NHARTS(NHARTS)) u_clint (
		.clk_i       (clk_i),
		.rst_ni      (rst_ni),
		.tick_i      (tick_i),
		.req_valid_i (d_valid & to_clint),
		.req_ready_o (c_ready),
		.req_addr_i  (a_addr[15:0]),
		.req_we_i    (a_we),
		.req_wstrb_i (a_wstrb),
		.req_wdata_i (a_wdata),
		.rsp_valid_o (c_rsp_v),
		.rsp_data_o  (c_rsp_d),
		.mtip_o      (mtip),
		.msip_o      (msip)
	);

	// everything else: the atomics unit, then out
	logic         m_ready, m_rsp_v;
	logic [X-1:0] m_rsp_d;

	amo_unit #(.NHARTS(NHARTS)) u_amo (
		.clk_i           (clk_i),
		.rst_ni          (rst_ni),
		.req_valid_i     (d_valid & ~to_clint),
		.req_ready_o     (m_ready),
		.req_addr_i      (a_addr),
		.req_we_i        (a_we),
		.req_wstrb_i     (a_wstrb),
		.req_wdata_i     (a_wdata),
		.req_amo_i       (a_amo),
		.req_atop_i      (a_atop),
		.req_id_i        (d_id),
		.rsp_valid_o     (m_rsp_v),
		.rsp_data_o      (m_rsp_d),
		.mem_req_valid_o (mem_d_req_valid_o),
		.mem_req_ready_i (mem_d_req_ready_i),
		.mem_req_addr_o  (mem_d_req_addr_o),
		.mem_req_we_o    (mem_d_req_we_o),
		.mem_req_wstrb_o (mem_d_req_wstrb_o),
		.mem_req_wdata_o (mem_d_req_wdata_o),
		.mem_rsp_valid_i (mem_d_rsp_valid_i),
		.mem_rsp_data_i  (mem_d_rsp_data_i)
	);

	assign d_ready = to_clint ? c_ready : m_ready;
	assign d_rsp_v = clint_q ? c_rsp_v : m_rsp_v;
	assign d_rsp_d = clint_q ? c_rsp_d : m_rsp_d;

endmodule : soc_top
