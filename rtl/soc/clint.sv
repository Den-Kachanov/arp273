// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

// CLINT: the machine timer and the software interrupts of every hart (ADR 019), at the offsets of the
// SiFive CLINT that Spike and QEMU also use (the SoC maps it at 0x0200_0000):
//
//   0x0000 + 4 h   msip[h]       bit 0: software interrupt of hart h (MSIP); other bits read 0
//   0x4000 + 8 h   mtimecmp[h]   64 bits; the timer interrupt of hart h (MTIP) is mtime >= mtimecmp[h]
//   0xBFF8         mtime         64 bits, counts up by one when tick_i is 1; writable
//
// Register port: the data-port protocol of the cores (valid/ready request, one response per request one
// cycle later; always ready). The address is aligned to XLEN/8 bytes and the strobes select bytes, so
// XLEN = 32 reaches the 64-bit registers as two halves and XLEN = 64 two msip words at once. Addresses
// of nothing read 0 and ignore writes. Only addr_i[15:0] is decoded: the interconnect selects the CLINT
//
// mtimecmp resets to all ones (no timer interrupt until software sets it), mtime and msip to 0. The
// outputs are registered: an interrupt line changes one cycle after mtime or mtimecmp does

module clint #(
	parameter int NHARTS = 1
) (
	input  logic                          clk_i,
	input  logic                          rst_ni,
	input  logic                          tick_i,       // mtime increment (a fixed-rate tick; 1 = every cycle)

	input  logic                          req_valid_i,
	output logic                          req_ready_o,
	input  logic [15:0]                   req_addr_i,
	input  logic                          req_we_i,
	input  logic [riscv_pkg::XLEN/8-1:0]  req_wstrb_i,
	input  logic [riscv_pkg::XLEN-1:0]    req_wdata_i,
	output logic                          rsp_valid_o,
	output logic [riscv_pkg::XLEN-1:0]    rsp_data_o,

	output logic [NHARTS-1:0]             mtip_o,
	output logic [NHARTS-1:0]             msip_o
);

	localparam int S  = riscv_pkg::XLEN / 8;      // bytes per port word
	localparam int SB = $clog2(S);

	logic [63:0]       mtime_q, mtime_d;
	logic [63:0]       mtimecmp_q [NHARTS];
	logic [63:0]       mtimecmp_d [NHARTS];
	logic [NHARTS-1:0] msip_q, msip_d;
	logic [S*8-1:0]    rdata;
	logic [15:0]       a;               // byte offset of one lane of the request

	assign req_ready_o = 1'b1;

	// the address is aligned to the port word (protocol); its low bits are 0 and not used
	logic unused_addr;
	assign unused_addr = &{1'b0, req_addr_i[SB-1:0]};

	// which register a byte offset belongs to: mtime at 0xBFF8, mtimecmp[h] at 0x4000 + 8 h (both by 8-byte
	// word, the low 3 bits pick the byte), msip[h] at 4 h (only its byte 0 holds a bit)
	function automatic logic is_mtime(input logic [12:0] word);
		is_mtime = (word == 13'h17FF);
	endfunction

	function automatic logic is_mtimecmp(input logic [12:0] word, input int h);
		is_mtimecmp = (word == 13'(32'h0800 + 32'(h)));
	endfunction

	function automatic logic is_msip(input logic [15:0] off, input int h);
		is_msip = (off == 16'(4 * h));
	endfunction

	// next state: the increment, then the bytes written by this cycle's request (a write to mtime
	// wins over the increment); and the read data of the request
	always_comb begin
		mtime_d = mtime_q + 64'(tick_i);
		msip_d  = msip_q;
		for (int h = 0; h < NHARTS; h++)
			mtimecmp_d[h] = mtimecmp_q[h];
		rdata = '0;

		for (int l = 0; l < S; l++) begin
			a = {req_addr_i[15:SB], SB'(l)};
			if (is_mtime(a[15:3]))
				rdata[8 * l +: 8] = mtime_q[8 * a[2:0] +: 8];
			for (int h = 0; h < NHARTS; h++) begin
				if (is_msip(a, h))
					rdata[8 * l +: 8] = {7'b0, msip_q[h]};
				if (is_mtimecmp(a[15:3], h))
					rdata[8 * l +: 8] = mtimecmp_q[h][8 * a[2:0] +: 8];
			end

			if (req_valid_i && req_we_i && req_wstrb_i[l]) begin
				if (is_mtime(a[15:3]))
					mtime_d[8 * a[2:0] +: 8] = req_wdata_i[8 * l +: 8];
				for (int h = 0; h < NHARTS; h++) begin
					if (is_msip(a, h))
						msip_d[h] = req_wdata_i[8 * l];
					if (is_mtimecmp(a[15:3], h))
						mtimecmp_d[h][8 * a[2:0] +: 8] = req_wdata_i[8 * l +: 8];
				end
			end
		end
	end

	always_ff @(posedge clk_i) begin
		if (!rst_ni) begin
			mtime_q     <= '0;
			msip_q      <= '0;
			rsp_valid_o <= 1'b0;
			for (int h = 0; h < NHARTS; h++)
				mtimecmp_q[h] <= '1;
		end else begin
			mtime_q     <= mtime_d;
			msip_q      <= msip_d;
			for (int h = 0; h < NHARTS; h++)
				mtimecmp_q[h] <= mtimecmp_d[h];
			rsp_valid_o <= req_valid_i;
		end
		// the read data: the registers before this cycle's write, as a memory does; a write reads 0
		if (req_valid_i)
			rsp_data_o <= req_we_i ? '0 : rdata;
	end

	// interrupt lines, registered
	always_ff @(posedge clk_i) begin
		if (!rst_ni) begin
			mtip_o <= '0;
			msip_o <= '0;
		end else begin
			for (int h = 0; h < NHARTS; h++)
				mtip_o[h] <= (mtime_q >= mtimecmp_q[h]);
			msip_o <= msip_q;
		end
	end

endmodule : clint
