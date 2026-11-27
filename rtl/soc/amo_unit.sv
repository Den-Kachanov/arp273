// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

// Atomics of the A extension on the memory side (ADR 019): sits between the arbiter and the memory, takes
// the requests of all harts (the data-port protocol with amo/atop and the hart id) and turns each atomic
// into plain accesses. The memory below sees only loads and stores. With the caches this logic moves into
// the L2 (ADR 019: atomics in L2).
//
//   load, store       passed through
//   LR                a load; the hart's reservation is set to its address
//   SC                with the hart's reservation at the same address: a store, response 0;
//                     otherwise nothing goes to memory and the response is 1 (in the lane of the access).
//                     Either way the hart's reservation is cleared
//   AMO               a read, then a write of the result, as one indivisible operation: the arbiter
//                     grants nobody else while it runs; the response is the old word
//
// Reservations: one per hart, at the byte address of the LR (the lowest strobe gives the lane, as Spike
// keeps the exact address). A store, a successful SC or an AMO of one hart clears the reservations of the
// other harts on the same aligned word: the reservation set is that word. A hart's own store does not
// clear its reservation (as in Spike); its SC does.
//
// The lanes: wstrb gives the bytes of the access (LR included); .W uses a 4-byte lane, .D the whole
// 64-bit word. An access takes one request at a time; a new one is accepted in the cycle the previous
// response leaves, except after an AMO or a failed SC (one cycle later).

module amo_unit #(
	parameter int NHARTS = 2,
	localparam int IW = (NHARTS > 1) ? $clog2(NHARTS) : 1
) (
	input  logic                          clk_i,
	input  logic                          rst_ni,

	input  logic                          req_valid_i,
	output logic                          req_ready_o,
	input  logic [riscv_pkg::XLEN-1:0]    req_addr_i,
	input  logic                          req_we_i,
	input  logic [riscv_pkg::XLEN/8-1:0]  req_wstrb_i,
	input  logic [riscv_pkg::XLEN-1:0]    req_wdata_i,
	input  logic                          req_amo_i,
	input  logic [4:0]                    req_atop_i,
	input  logic [IW-1:0]                 req_id_i,
	output logic                          rsp_valid_o,
	output logic [riscv_pkg::XLEN-1:0]    rsp_data_o,

	output logic                          mem_req_valid_o,
	input  logic                          mem_req_ready_i,
	output logic [riscv_pkg::XLEN-1:0]    mem_req_addr_o,
	output logic                          mem_req_we_o,
	output logic [riscv_pkg::XLEN/8-1:0]  mem_req_wstrb_o,
	output logic [riscv_pkg::XLEN-1:0]    mem_req_wdata_o,
	input  logic                          mem_rsp_valid_i,
	input  logic [riscv_pkg::XLEN-1:0]    mem_rsp_data_i
);

	localparam int X  = riscv_pkg::XLEN;
	localparam int S  = X / 8;
	localparam int SB = $clog2(S);

	typedef enum logic [2:0] {
		ST_IDLE,     // no request in flight
		ST_PASS,     // a load, store, LR or successful SC is in memory: its response goes up
		ST_SCFAIL,   // a failed SC: respond 1 now
		ST_AMO_RD,   // AMO: waiting for the old word
		ST_AMO_WR,   // AMO: the write of the result is offered to memory
		ST_AMO_WAIT  // AMO: waiting for the write's response
	} state_t;

	state_t        state_q;
	logic          sc_q;                       // the access in ST_PASS is a successful SC
	logic [SB-1:0] lane_q;                     // byte offset of the access
	logic [X-1:0]  addr_q, wdata_q, old_q;
	logic [S-1:0]  wstrb_q;
	logic [4:0]    atop_q;

	logic [NHARTS-1:0] resv_valid_q;
	logic [X-1:0]      resv_addr_q [NHARTS];

	// the request now
	logic          accept;
	logic          is_lr, is_sc, is_amo, sc_ok;
	logic [SB-1:0] lane;
	logic [X-1:0]  byte_addr;
	logic          writes;                     // the request stores something (clears other reservations)

	always_comb begin
		lane = '0;
		for (int l = S - 1; l >= 0; l--)
			if (req_wstrb_i[l]) lane = SB'(l);
	end

	assign byte_addr = {req_addr_i[X-1:SB], lane};
	assign is_lr     = req_amo_i & (req_atop_i == riscv_pkg::AMO_LR);
	assign is_sc     = req_amo_i & (req_atop_i == riscv_pkg::AMO_SC);
	assign is_amo    = req_amo_i & ~is_lr & ~is_sc;
	assign sc_ok     = resv_valid_q[req_id_i] & (resv_addr_q[req_id_i] == byte_addr);
	assign writes    = (req_we_i & ~req_amo_i) | (is_sc & sc_ok) | is_amo;

	// a new request is taken when nothing is in flight, or in the cycle a passed-through access responds
	assign req_ready_o = (state_q == ST_IDLE || (state_q == ST_PASS && mem_rsp_valid_i)) &&
	                     ((is_sc && !sc_ok) || mem_req_ready_i);
	assign accept      = req_valid_i & req_ready_o;

	// the operation of an AMO on the old word (old_q) and the operand (wdata_q), in the lane
	logic [X-1:0] a, b, r, a_s, b_s;
	logic         is_w;

	assign is_w = (X == 32) || (wstrb_q != '1);
	always_comb begin
		a = old_q >> (8 * lane_q);
		b = wdata_q >> (8 * lane_q);
		if (is_w) begin
			// .W: 32-bit operands, sign-extended for the signed compare, zero-extended for the rest
			a_s = X'($signed(a[31:0]));
			b_s = X'($signed(b[31:0]));
			a   = X'(a[31:0]);
			b   = X'(b[31:0]);
		end else begin
			a_s = a;
			b_s = b;
		end
		case (atop_q)
			riscv_pkg::AMO_SWAP: r = b;
			riscv_pkg::AMO_XOR:  r = a ^ b;
			riscv_pkg::AMO_OR:   r = a | b;
			riscv_pkg::AMO_AND:  r = a & b;
			riscv_pkg::AMO_MIN:  r = ($signed(a_s) < $signed(b_s)) ? a : b;
			riscv_pkg::AMO_MAX:  r = ($signed(a_s) > $signed(b_s)) ? a : b;
			riscv_pkg::AMO_MINU: r = (a < b) ? a : b;
			riscv_pkg::AMO_MAXU: r = (a > b) ? a : b;
			default:             r = a + b;              // AMO_ADD
		endcase
	end

	// to memory: the request as it is (an AMO starts as a load), or the AMO's write
	always_comb begin
		mem_req_valid_o = 1'b0;
		mem_req_addr_o  = req_addr_i;
		mem_req_we_o    = req_we_i & ~is_amo & ~is_lr;
		mem_req_wstrb_o = req_wstrb_i;
		mem_req_wdata_o = req_wdata_i;
		if (state_q == ST_AMO_WR) begin
			mem_req_valid_o = 1'b1;
			mem_req_addr_o  = addr_q;
			mem_req_we_o    = 1'b1;
			mem_req_wstrb_o = wstrb_q;
			mem_req_wdata_o = r << (8 * lane_q);
		end else if (state_q == ST_IDLE || (state_q == ST_PASS && mem_rsp_valid_i)) begin
			mem_req_valid_o = req_valid_i & ~(is_sc & ~sc_ok);
		end
	end

	// up: the response
	always_comb begin
		rsp_valid_o = 1'b0;
		rsp_data_o  = mem_rsp_data_i;
		case (state_q)
			ST_PASS: begin
				rsp_valid_o = mem_rsp_valid_i;
				if (sc_q) rsp_data_o = '0;
			end
			ST_SCFAIL: begin
				rsp_valid_o = 1'b1;
				rsp_data_o  = X'(1) << (8 * lane_q);
			end
			ST_AMO_WAIT: begin
				rsp_valid_o = mem_rsp_valid_i;
				rsp_data_o  = old_q;
			end
			default: begin end
		endcase
	end

	always_ff @(posedge clk_i) begin
		if (!rst_ni) begin
			state_q      <= ST_IDLE;
			resv_valid_q <= '0;
		end else begin
			case (state_q)
				ST_PASS:   if (mem_rsp_valid_i && !accept) state_q <= ST_IDLE;
				ST_SCFAIL: state_q <= ST_IDLE;
				ST_AMO_RD: if (mem_rsp_valid_i) state_q <= ST_AMO_WR;
				ST_AMO_WR: if (mem_req_ready_i) state_q <= ST_AMO_WAIT;
				ST_AMO_WAIT: if (mem_rsp_valid_i) state_q <= ST_IDLE;
				default: begin end
			endcase
			if (accept) begin
				if (is_amo)              state_q <= ST_AMO_RD;
				else if (is_sc && !sc_ok) state_q <= ST_SCFAIL;
				else                     state_q <= ST_PASS;

				// reservations: the other harts lose theirs on a written word; LR sets, SC clears its own
				if (writes)
					for (int h = 0; h < NHARTS; h++)
						if (IW'(h) != req_id_i && resv_addr_q[h][X-1:SB] == req_addr_i[X-1:SB])
							resv_valid_q[h] <= 1'b0;
				if (is_lr) begin
					resv_valid_q[req_id_i] <= 1'b1;
					resv_addr_q[req_id_i]  <= byte_addr;
				end
				if (is_sc)
					resv_valid_q[req_id_i] <= 1'b0;
			end
		end
		if (accept) begin
			sc_q    <= is_sc & sc_ok;
			lane_q  <= lane;
			addr_q  <= req_addr_i;
			wstrb_q <= req_wstrb_i;
			wdata_q <= req_wdata_i;
			atop_q  <= req_atop_i;
		end
		if (state_q == ST_AMO_RD && mem_rsp_valid_i)
			old_q <= mem_rsp_data_i;
	end

endmodule : amo_unit
