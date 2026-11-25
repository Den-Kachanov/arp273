// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

// Multiplier and divider of the M extension (ADR 009, ADR 011): one iterative unit, one bit per cycle
//
// Protocol: the core holds valid_i, funct3_i, word_i, a_i and b_i stable until done_o.
// The first cycle loads the operands, then N cycles iterate, then done_o is 1 for one cycle
// with result_o valid (the core commits in that cycle). The unit returns to idle by itself,
// so the next instruction can raise valid_i in the cycle after done_o.
// Latency from valid_i to done_o: N + 1 clock edges, where N = XLEN / 2 on the short path, XLEN otherwise
//
// Short path (ADR 011): XLEN / 2 iterations are enough when the operands fit in half a word:
//   MUL (also MULW) whose multiplier b has a zero upper half: the low product is ready after XLEN / 2 steps
//   DIV, DIVU, REM, REMU (also the W forms) whose dividend magnitude is below 2^(XLEN/2), divisor not zero:
//   the dividend is loaded shifted up by XLEN / 2, so the steps that would only shift zeros are skipped
// MULH, MULHSU, MULHU and division by zero always take the full path
//
// Multiply: shift-and-add on the pair {hi, lo}; lo starts as the multiplier and is replaced
// bit by bit by the low part of the product. A signed multiplier subtracts in the last step
// (its top bit has a negative weight), a signed multiplicand is sign-extended to XLEN + 1 bits
//
// Divide: restoring division on magnitudes; signs are fixed at the end, so DIV, REM and the
// special cases follow from one unsigned divider:
//   division by zero:  quotient all ones, remainder = dividend (the divider gives this by itself)
//   overflow (MIN / -1): quotient MIN, remainder 0 (magnitude 2^(XLEN-1) fits unsigned)
//
// One adder/subtractor serves both operations

module muldiv (
	input  logic                       clk_i,
	input  logic                       rst_ni,

	input  logic                       valid_i,
	// abandon the operation (the pipeline flushed the instruction): back to idle in the next cycle
	input  logic                       abort_i,
	input  logic [2:0]                 funct3_i,
	input  logic                       word_i,
	input  logic [riscv_pkg::XLEN-1:0] a_i,
	input  logic [riscv_pkg::XLEN-1:0] b_i,

	output logic                       done_o,
	output logic [riscv_pkg::XLEN-1:0] result_o
);

	localparam int X  = riscv_pkg::XLEN;
	localparam int H  = X / 2;
	localparam int CW = $clog2(X);

	typedef enum logic [1:0] {
		S_IDLE = 2'b00,
		S_RUN  = 2'b01,
		S_DONE = 2'b10
	} state_t;

	// funct3: 000 MUL, 001 MULH, 010 MULHSU, 011 MULHU, 100 DIV, 101 DIVU, 110 REM, 111 REMU
	logic is_div;
	logic a_signed;
	logic b_signed;
	logic pick_hi;

	assign is_div = funct3_i[2];

	always_comb begin
		a_signed = 1'b0;
		b_signed = 1'b0;

		case (funct3_i)
			3'b001, 3'b100, 3'b110: begin
				a_signed = 1'b1;
				b_signed = 1'b1;
			end
			3'b010:  a_signed = 1'b1;
			default: begin end
		endcase // funct3_i
	end

	// the result is the high half of the product (MULH*), the remainder (REM*), or the low half / quotient
	assign pick_hi = funct3_i[2] ? funct3_i[1] : (funct3_i[1] | funct3_i[0]);

	// operands: a W instruction works on the low 32 bits, extended to XLEN by the operand's signedness
	logic [X-1:0] a_eff;
	logic [X-1:0] b_eff;
	logic         word_q;

	generate
		if (X == 64) begin : g_rv64
			assign a_eff = word_i ? {{(X-32){a_signed & a_i[31]}}, a_i[31:0]} : a_i;
			assign b_eff = word_i ? {{(X-32){b_signed & b_i[31]}}, b_i[31:0]} : b_i;
		end else begin : g_rv32
			assign a_eff = a_i;
			assign b_eff = b_i;
		end
	endgenerate

	// magnitudes for the divider
	logic         sign_a;
	logic         sign_b;
	logic [X-1:0] mag_a;
	logic [X-1:0] mag_b;
	logic         b_is_zero;

	assign sign_a    = a_signed & a_eff[X-1];
	assign sign_b    = b_signed & b_eff[X-1];
	assign mag_a     = sign_a ? (~a_eff + X'(1)) : a_eff;
	assign mag_b     = sign_b ? (~b_eff + X'(1)) : b_eff;
	assign b_is_zero = (b_eff == '0);

	// short path. "The dividend magnitude is below 2^H" is read from the operand bits, not from mag_a,
	// so it does not wait for the negation: a non-negative value needs a zero upper half; a negative one
	// needs an all-ones upper half and a non-zero lower half (-2^H itself has magnitude 2^H)
	logic a_fits;
	logic short_mul;
	logic short_div;
	logic short_op;

	assign a_fits    = sign_a ? (&a_eff[X-1:H] & |a_eff[H-1:0]) : ~|a_eff[X-1:H];
	assign short_mul = ~is_div & ~pick_hi & ~|b_eff[X-1:H];
	assign short_div = is_div & a_fits & ~b_is_zero;
	assign short_op  = short_mul | short_div;

	// the dividend as the divider sees it: on the short path its H significant bits move to the top
	logic [X-1:0] dividend;

	assign dividend = short_div ? {mag_a[H-1:0], {H{1'b0}}} : mag_a;

	// state
	state_t        state_q;
	logic [X:0]    hi_q;
	logic [X-1:0]  lo_q;
	logic [X:0]    opa_q;
	logic [CW-1:0] cnt_q;
	logic          is_div_q;
	logic          a_signed_q;
	logic          b_signed_q;
	logic          pick_hi_q;
	logic          neg_q;
	logic          short_q;
	logic          last_q;

	// shared adder: hi + y or hi - y in XLEN + 1 bits (two's complement)
	// multiply: y = multiplicand if the current multiplier bit is 1, subtract in the last step of a signed multiplier
	// divide:   y = divisor, always subtract (the trial subtraction)
	// In division both hi and the divisor are below 2^XLEN (the dividend has XLEN bits, so a partial remainder
	// after j steps is below 2^j), hence hi - y lies in (-2^XLEN, 2^XLEN) and sum[X] is exactly the borrow
	// last_q marks the last iteration. It is a register set one cycle ahead (cnt_q == 1), so the counter
	// compare is not in front of the adder: the path starts at a register and goes straight into the carry chain
	logic          y_en;
	logic          sub;
	logic [X:0]    y;
	logic [X:0]    sum;

	assign y_en = is_div_q | lo_q[0];
	assign sub  = is_div_q | (last_q & b_signed_q);
	assign y    = y_en ? opa_q : '0;
	assign sum  = hi_q + (y ^ {(X+1){sub}}) + {{X{1'b0}}, sub};

	// next values while running
	logic [X:0]   mul_hi;
	logic [X-1:0] mul_lo;
	logic         ge;
	logic [X-1:0] rem_n;
	logic [X:0]   div_hi;
	logic [X-1:0] div_lo;

	// multiply: arithmetic shift right of {sum, lo} for a signed multiplicand, logical for an unsigned one
	assign mul_hi = {a_signed_q & sum[X], sum[X:1]};
	assign mul_lo = {sum[0], lo_q[X-1:1]};

	// divide: the trial subtraction succeeded when there is no borrow; the next dividend bit enters from lo.
	// The new remainder is below the divisor, so it fits in XLEN bits
	assign ge     = ~sum[X];
	assign rem_n  = ge ? sum[X-1:0] : hi_q[X-1:0];
	assign div_hi = {rem_n, lo_q[X-1]};
	assign div_lo = {lo_q[X-2:0], ge};

	always_ff @(posedge clk_i) begin
		if (!rst_ni || abort_i) begin
			state_q <= S_IDLE;
		end else begin
			case (state_q)
				S_IDLE:  if (valid_i) state_q <= S_RUN;
				S_RUN:   if (last_q)  state_q <= S_DONE;
				S_DONE:               state_q <= S_IDLE;
				default:              state_q <= S_IDLE;
			endcase // state_q
		end
	end

	// datapath registers need no reset: they are written before they are read
	always_ff @(posedge clk_i) begin
		if (state_q == S_IDLE && valid_i) begin
			// the divider starts with the first dividend bit already in hi
			hi_q       <= is_div ? {{X{1'b0}}, dividend[X-1]} : '0;
			lo_q       <= is_div ? {dividend[X-2:0], 1'b0} : b_eff;
			opa_q      <= is_div ? {1'b0, mag_b} : {a_signed & a_eff[X-1], a_eff};
			cnt_q      <= short_op ? CW'(H - 1) : CW'(X - 1);
			last_q     <= 1'b0;
			short_q    <= short_op;
			is_div_q   <= is_div;
			a_signed_q <= a_signed;
			b_signed_q <= b_signed;
			pick_hi_q  <= pick_hi;
			word_q     <= word_i;
			// the quotient is negative when the signs differ (not for division by zero: all ones);
			// the remainder takes the sign of the dividend
			neg_q      <= is_div & (funct3_i[1] ? sign_a : ((sign_a ^ sign_b) & ~b_is_zero));
		end else if (state_q == S_RUN) begin
			hi_q  <= is_div_q ? div_hi : mul_hi;
			lo_q  <= is_div_q ? div_lo : mul_lo;
			cnt_q  <= cnt_q - CW'(1);
			last_q <= (cnt_q == CW'(1));
		end
	end

	// result: after the last step the divider has shifted hi once more, so the remainder is hi[X:1].
	// Short multiply: after H steps the product is hi * 2^H + lo[X-1:H], so its low half is {hi[H-1:0], lo[X-1:H]}.
	// Short divide: the quotient is in lo as usual (the bits above it are the zeros loaded below the dividend)
	logic [X-1:0] picked;
	logic [X-1:0] signed_res;

	always_comb begin
		picked = lo_q;
		if (pick_hi_q)                 picked = is_div_q ? hi_q[X:1] : hi_q[X-1:0];
		else if (short_q && !is_div_q) picked = {hi_q[H-1:0], lo_q[X-1:H]};
	end

	assign signed_res = neg_q ? (~picked + X'(1)) : picked;

	generate
		if (X == 64) begin : g_res64
			assign result_o = word_q ? {{(X-32){signed_res[31]}}, signed_res[31:0]} : signed_res;
		end else begin : g_res32
			logic unused_word;

			assign unused_word = &{1'b0, word_q};
			assign result_o    = signed_res;
		end
	endgenerate

	assign done_o = (state_q == S_DONE);

endmodule : muldiv
