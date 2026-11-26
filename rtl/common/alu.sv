// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

module alu (
	input  logic [riscv_pkg::XLEN-1:0] a_i,
	input  logic [riscv_pkg::XLEN-1:0] b_i,
	input  riscv_pkg::alu_op_t         op_i,
	input  logic                       word_i,
	output logic [riscv_pkg::XLEN-1:0] r_o
);

	// Contract with the decoder: op_i is one of the 10 codes of alu_op_t, word_i = 1 only with
	// ADD, SUB, SLL, SRL, SRA. Other codes are never sent; their result is not defined here (ADR 010)

	localparam int X   = riscv_pkg::XLEN;
	localparam int SHW = $clog2(X);

	// ORIGINAL: one expression per operation (ADR 007), kept as the readable specification
	// always_comb begin
	// 	r_full = '0;
	//
	// 	case (op_i)
	// 		riscv_pkg::ALU_ADD:  r_full = a_i + b_i;
	// 		riscv_pkg::ALU_SUB:  r_full = a_i - b_i;
	// 		riscv_pkg::ALU_AND:  r_full = a_i & b_i;
	// 		riscv_pkg::ALU_OR:   r_full = a_i | b_i;
	// 		riscv_pkg::ALU_XOR:  r_full = a_i ^ b_i;
	// 		riscv_pkg::ALU_SLL:  r_full = a_sh << shamt;
	// 		riscv_pkg::ALU_SRL:  r_full = a_sh >> shamt;
	// 		riscv_pkg::ALU_SRA:  r_full = $signed(a_sh) >>> shamt;
	// 		riscv_pkg::ALU_SLT:  r_full = {{(riscv_pkg::XLEN-1){1'b0}}, $signed(a_i) < $signed(b_i)};
	// 		riscv_pkg::ALU_SLTU: r_full = {{(riscv_pkg::XLEN-1){1'b0}}, a_i < b_i};
	// 		default:             r_full = '0;
	// 	endcase // op_i
	// end

	// Operation select straight from the encoding {funct7[5], funct3}: funct3 names the
	// operation, funct7[5] only tells SUB from ADD and SRA from SRL
	logic [2:0] f3;
	logic       alt;
	logic       is_add, is_sll, is_slt, is_cmp, is_xor, is_shr, is_or, is_and, is_sub, is_sra;

	assign f3     = op_i[2:0];
	assign alt    = op_i[3];
	assign is_add = f3 == 3'b000;      // ADD or SUB
	assign is_sll = f3 == 3'b001;
	assign is_slt = f3 == 3'b010;
	assign is_cmp = f3[2:1] == 2'b01;  // SLT or SLTU
	assign is_xor = f3 == 3'b100;
	assign is_shr = f3 == 3'b101;      // SRL or SRA
	assign is_or  = f3 == 3'b110;
	assign is_and = f3 == 3'b111;
	assign is_sub = (is_add & alt) | is_cmp;
	assign is_sra = is_shr & alt;

	// One X+1-bit adder for ADD, SUB, SLT, SLTU. For the comparisons the operands are extended
	// by one bit (sign for SLT, zero for SLTU), so a - b never overflows and its bit X is the
	// answer to a < b in both cases: no separate comparator and no mux between signed and unsigned
	logic       a_ext;
	logic       b_ext;
	logic [X:0] sum;

	assign a_ext = is_slt & a_i[X-1];
	assign b_ext = is_slt & b_i[X-1];
	assign sum   = {a_ext, a_i} + ({b_ext, b_i} ^ {(X+1){is_sub}}) + (X+1)'(is_sub);

	// One right shifter. SLL = reverse the input, shift right, reverse the output (the output
	// reverse is only wiring, picked in the result mux)
	logic [SHW-1:0] shamt;
	logic [X-1:0]   sh_in;
	logic           fill;
	logic [X-1:0]   sh_r;
	logic [X-1:0]   r_full;

	if (X == 64) begin : g_rv64
		// W shifts: 5-bit amount; SRLW and SRAW shift the low word with zeros or a_i[31] above it
		always_comb begin
			shamt = word_i ? {1'b0, b_i[4:0]} : b_i[SHW-1:0];
			for (int i = 0; i < X; i++) begin
				if (is_sll)                 sh_in[i] = a_i[X-1-i];
				else if (word_i && i >= 32) sh_in[i] = is_sra & a_i[31];
				else                        sh_in[i] = a_i[i];
			end
		end

		// W result: the low word, sign-extended
		assign r_o = word_i ? {{32{r_full[31]}}, r_full[31:0]} : r_full;
	end else begin : g_rv32
		logic unused_word;

		always_comb begin
			shamt = b_i[SHW-1:0];
			for (int i = 0; i < X; i++) sh_in[i] = is_sll ? a_i[X-1-i] : a_i[i];
		end

		assign r_o         = r_full;
		assign unused_word = &{1'b0, word_i};
	end

	// SRA fills with a_i[X-1]. For SRAW the amount is at most 31, so the fill never reaches the
	// low word, and the upper word is replaced by the sign extension anyway: no word_i mux here
	assign fill = is_sra & a_i[X-1];
	assign sh_r = X'($signed({fill, sh_in}) >>> shamt);

	// Result: AND-OR of one-hot selects instead of a priority case, so every bit is a flat
	// function of its own inputs. The comparison result lands in bit 0
	always_comb begin
		for (int i = 0; i < X; i++) begin
			r_full[i] = (is_add & sum[i])
			          | (is_xor & (a_i[i] ^ b_i[i]))
			          | (is_or  & (a_i[i] | b_i[i]))
			          | (is_and & (a_i[i] & b_i[i]))
			          | (is_sll & sh_r[X-1-i])
			          | (is_shr & sh_r[i]);
		end
		r_full[0] = r_full[0] | (is_cmp & sum[X]);
	end

endmodule : alu
