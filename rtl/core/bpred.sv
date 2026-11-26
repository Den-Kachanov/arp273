// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

// Branch predictor of the fetch stage (ADR 018): a BHT of 2-bit counters, a direct-mapped BTB and a
// return stack. It only guesses: EX checks every guess, so a wrong or stale entry costs cycles, never
// correctness. That is why the tags and the stored targets may be partial (TAG_BITS, TARGET_BITS).
//
// Lookup (combinational from pc_i, the fetch address): taken_o when the BTB holds pc_i and the entry is
// a jump, a call, a return, or a branch whose counter is 2 or 3. target_o is the BTB target, or for a
// return the top of the return stack. When the fetch is accepted (accept_i), a predicted call pushes
// pc_i + 4 and a predicted return pops.
//
// Update (from EX, one resolved control instruction): a taken one is written into the BTB (if its target
// shares the pc bits above TARGET_BITS); a branch also updates its counter. A new branch entry starts
// its counter at 2 (weakly taken), so a counter is never read before it is written, and the BHT needs
// no reset. Only the BTB valid bits and the return stack pointer are reset.
//
// Kinds: 0 branch, 1 jump (JAL or JALR that is neither call nor return), 2 call (rd = x1 or x5),
// 3 return (JALR with rs1 = x1 or x5 and rd = x0)

module bpred #(
	parameter int BHT_ENTRIES = 64,  // power of two
	parameter int BTB_ENTRIES = 16,  // power of two
	parameter int RAS_DEPTH   = 8,   // power of two
	parameter int TAG_BITS    = 10,  // pc bits above the BTB index that are compared
	parameter int TARGET_BITS = 20   // low target bits stored; the bits above come from the pc
) (
	input  logic                       clk_i,
	input  logic                       rst_ni,

	input  logic [riscv_pkg::XLEN-1:0] pc_i,
	output logic                       taken_o,
	output logic [riscv_pkg::XLEN-1:0] target_o,
	input  logic                       accept_i,

	input  logic                       upd_i,
	input  logic [riscv_pkg::XLEN-1:0] upd_pc_i,
	input  logic [1:0]                 upd_kind_i,
	input  logic                       upd_taken_i,
	input  logic [riscv_pkg::XLEN-1:0] upd_target_i
);

	localparam int X  = riscv_pkg::XLEN;
	localparam int IB = $clog2(BTB_ENTRIES);
	localparam int HB = $clog2(BHT_ENTRIES);
	localparam int RB = $clog2(RAS_DEPTH);
	localparam int TW = TARGET_BITS - 2;  // stored target bits (bits 1:0 are 0)

	// The sizes must be powers of two, the stack must have two entries at least, the tag must end below the
	// stored target bits (the target's upper bits come from the pc), and the BHT index must lie within the
	// BTB index and tag: then a BTB hit names one branch, whose counter was written when it was entered
	generate
		if (BHT_ENTRIES != (1 << HB) || BTB_ENTRIES != (1 << IB) || RAS_DEPTH != (1 << RB) || RAS_DEPTH < 2)
		begin : g_bad_size
			$error("bpred: BHT_ENTRIES, BTB_ENTRIES and RAS_DEPTH must be powers of two, RAS_DEPTH at least 2");
		end
		if (2 + IB + TAG_BITS > TARGET_BITS || TARGET_BITS > X) begin : g_bad_bits
			$error("bpred: the tag (pc bits 2 + log2(BTB_ENTRIES) + TAG_BITS) must end below TARGET_BITS <= XLEN");
		end
		if (HB > IB + TAG_BITS) begin : g_bad_bht
			$error("bpred: log2(BHT_ENTRIES) must not exceed log2(BTB_ENTRIES) + TAG_BITS");
		end
	endgenerate

	localparam logic [1:0] K_BR   = 2'd0;
	localparam logic [1:0] K_CALL = 2'd2;
	localparam logic [1:0] K_RET  = 2'd3;

	logic                btb_valid_q [BTB_ENTRIES];
	logic [TAG_BITS-1:0] btb_tag_q   [BTB_ENTRIES];
	logic [TW-1:0]       btb_tgt_q   [BTB_ENTRIES];
	logic [1:0]          btb_kind_q  [BTB_ENTRIES];
	logic [1:0]          bht_q       [BHT_ENTRIES];
	logic [TW-1:0]       ras_q       [RAS_DEPTH];
	logic [RB-1:0]       ras_ptr_q;          // next free slot; the top is ras_ptr_q - 1

	// lookup
	logic [IB-1:0]       idx;
	logic [HB-1:0]       hidx;
	logic                hit;
	logic [1:0]          kind;
	logic [TW-1:0]       tgt_lo;
	logic [RB-1:0]       top;

	assign idx     = pc_i[2 +: IB];
	assign hidx    = pc_i[2 +: HB];
	assign hit     = btb_valid_q[idx] & (btb_tag_q[idx] == pc_i[2+IB +: TAG_BITS]);
	assign kind    = btb_kind_q[idx];
	assign top     = ras_ptr_q - RB'(1);
	assign tgt_lo  = (kind == K_RET) ? ras_q[top] : btb_tgt_q[idx];
	assign taken_o = hit & ((kind != K_BR) | bht_q[hidx][1]);
	assign target_o = {pc_i[X-1:TARGET_BITS], tgt_lo, 2'b00};

	// update
	logic [IB-1:0]       u_idx;
	logic [HB-1:0]       u_hidx;
	logic [TAG_BITS-1:0] u_tag;
	logic                u_hit;
	logic                u_fits;
	logic                u_write;
	logic [X-1:0]        push_pc;

	assign u_idx   = upd_pc_i[2 +: IB];
	assign u_hidx  = upd_pc_i[2 +: HB];
	assign u_tag   = upd_pc_i[2+IB +: TAG_BITS];
	assign u_hit   = btb_valid_q[u_idx] & (btb_tag_q[u_idx] == u_tag);
	// a return's target comes from the stack, so it need not fit
	assign u_fits  = (upd_kind_i == K_RET) | (upd_target_i[X-1:TARGET_BITS] == upd_pc_i[X-1:TARGET_BITS]);
	assign u_write = upd_i & upd_taken_i & u_fits;
	assign push_pc = pc_i + X'(4);

	// bits the predictor does not need: the byte offset (instructions are word aligned), the pc bits
	// between the tag and the target bits of an update, the upper bits of pc + 4 (taken from the pc)
	logic unused_bits;

	assign unused_bits = &{1'b0, upd_pc_i[1:0], upd_target_i[1:0], push_pc[1:0], push_pc[X-1:TARGET_BITS],
	                       upd_pc_i[TARGET_BITS-1:2+IB+TAG_BITS], pc_i[1:0]};

	always_ff @(posedge clk_i) begin
		if (!rst_ni) begin
			for (int i = 0; i < BTB_ENTRIES; i++) btb_valid_q[i] <= 1'b0;
			ras_ptr_q <= '0;
		end else begin
			if (u_write) btb_valid_q[u_idx] <= 1'b1;
			if (accept_i && hit && kind == K_CALL)     ras_ptr_q <= ras_ptr_q + RB'(1);
			else if (accept_i && hit && kind == K_RET) ras_ptr_q <= top;
		end
	end

	// no reset: written before they are read (see the header)
	always_ff @(posedge clk_i) begin
		if (u_write) begin
			btb_tag_q[u_idx]  <= u_tag;
			btb_tgt_q[u_idx]  <= upd_target_i[TARGET_BITS-1:2];
			btb_kind_q[u_idx] <= upd_kind_i;
		end
		if (upd_i && upd_kind_i == K_BR) begin
			if (u_hit) begin
				if (upd_taken_i && bht_q[u_hidx] != 2'd3)       bht_q[u_hidx] <= bht_q[u_hidx] + 2'd1;
				else if (!upd_taken_i && bht_q[u_hidx] != 2'd0) bht_q[u_hidx] <= bht_q[u_hidx] - 2'd1;
			end else if (u_write) begin
				bht_q[u_hidx] <= 2'd2;
			end
		end
		if (accept_i && hit && kind == K_CALL) ras_q[ras_ptr_q] <= push_pc[TARGET_BITS-1:2];
	end

endmodule : bpred
