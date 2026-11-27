// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

// Round-robin arbiter: N masters share one port of the memory protocol of the cores (ADR 013: valid/ready
// request, one response per request at least a cycle later). One request is in flight at a time: a new
// one is granted only when the previous response arrives (in that same cycle, as the cores do). The
// response goes back to the master that sent the request; id_o names it downstream (the atomics unit
// keeps a reservation per hart by it).
//
// Fairness: after a grant, the master after the granted one has the highest priority, so every master
// that keeps its request up is served within N grants. A request that is not granted may change or be
// withdrawn, as the protocol allows.
//
// In the SoC of stage 2 step 3 this is the whole interconnect (ADR 019); with the caches it becomes the
// arbitration in front of the L2.

module rr_arbiter #(
	parameter int N  = 2,                 // masters
	parameter int W  = 8,                 // request payload bits (address, write data, ...)
	parameter int RW = 8,                 // response data bits
	localparam int IW = (N > 1) ? $clog2(N) : 1
) (
	input  logic              clk_i,
	input  logic              rst_ni,

	input  logic [N-1:0]      req_valid_i,
	output logic [N-1:0]      req_ready_o,
	input  logic [N*W-1:0]    req_data_i,     // master m in bits [m*W +: W]
	output logic [N-1:0]      rsp_valid_o,
	output logic [RW-1:0]     rsp_data_o,     // the same for all; rsp_valid_o says whose it is

	output logic              req_valid_o,
	input  logic              req_ready_i,
	output logic [W-1:0]      req_data_o,
	output logic [IW-1:0]     req_id_o,
	input  logic              rsp_valid_i,
	input  logic [RW-1:0]     rsp_data_i
);

	logic          busy_q;                  // a request is in flight
	logic [IW-1:0] owner_q;                 // whose
	logic [IW-1:0] prio_q;                  // the master with the highest priority now
	logic          free;                    // a request may be granted in this cycle
	logic          found;
	logic [IW-1:0] pick;
	logic [IW-1:0] m;                       // a candidate in the search below

	assign free = ~busy_q | rsp_valid_i;

	// the first requesting master at or after prio_q
	always_comb begin
		found = 1'b0;
		pick  = prio_q;
		m     = '0;
		for (int k = 0; k < N; k++) begin
			m = IW'((32'(prio_q) + k) % N);
			if (!found && req_valid_i[m]) begin
				found = 1'b1;
				pick  = m;
			end
		end
	end

	assign req_valid_o = free & found;
	assign req_data_o  = req_data_i[32'(pick) * W +: W];
	assign req_id_o    = pick;

	always_comb begin
		req_ready_o = '0;
		req_ready_o[pick] = free & found & req_ready_i;
	end

	always_comb begin
		rsp_valid_o = '0;
		rsp_valid_o[owner_q] = busy_q & rsp_valid_i;
	end
	assign rsp_data_o = rsp_data_i;

	always_ff @(posedge clk_i) begin
		if (!rst_ni) begin
			busy_q  <= 1'b0;
			owner_q <= '0;
			prio_q  <= '0;
		end else begin
			if (req_valid_o && req_ready_i) begin
				busy_q  <= 1'b1;
				owner_q <= pick;
				prio_q  <= IW'((32'(pick) + 1) % N);
			end else if (rsp_valid_i) begin
				busy_q <= 1'b0;
			end
		end
	end

endmodule : rr_arbiter
