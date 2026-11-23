// SPDX-License-Identifier: CERN-OHL-S-2.0

module alu (
	input  logic [riscv_pkg::XLEN-1:0] a_i,
	input  logic [riscv_pkg::XLEN-1:0] b_i,
	input  riscv_pkg::alu_op_t         op_i,
	input  logic					   word_i,
	output logic [riscv_pkg::XLEN-1:0] r_o
);

	localparam int X   = riscv_pkg::XLEN;
	localparam int SHW = $clog2(X);

	logic [X-1:0]   a_sh;
	logic [SHW-1:0] shamt;
	logic [X-1:0]   r_full;

	logic [X-1:0] rev_in;
	logic [X-1:0] sh_in;
	logic [X-1:0] sh_r;
	logic [X-1:0] sh_out;
	logic         sub;
	logic         fill;
	logic         lt;
	logic         ltu;
	logic [X:0]   sum;

	if (X == 64) begin : g_rv64
		always_comb begin
			a_sh  = a_i;
			shamt = b_i[SHW-1:0];
			if (word_i) begin
				shamt = {1'b0, b_i[4:0]};
				if (op_i == riscv_pkg::ALU_SRL) a_sh = {32'b0, a_i[31:0]};
				if (op_i == riscv_pkg::ALU_SRA) a_sh = {{32{a_i[31]}}, a_i[31:0]};
			end
		end

		assign r_o = word_i ? {{32{r_full[31]}}, r_full[31:0]} : r_full;
	end else begin : g_rv32
		logic unused_word;

		assign a_sh        = a_i;
		assign shamt       = b_i[SHW-1:0];
		assign r_o         = r_full;
		assign unused_word = &{1'b0, word_i};
	end


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

	// Yosys 0.66, XLEN = 64: LUT6 983 -> 763
	always_comb begin
		// a - b = a + ~b + 1
		sub = (op_i == riscv_pkg::ALU_SUB) | (op_i == riscv_pkg::ALU_SLT) | (op_i == riscv_pkg::ALU_SLTU);
		sum = {1'b0, a_i} + {1'b0, b_i ^ {X{sub}}} + {{X{1'b0}}, sub};

		// unsigned a < b: no carry out of a - b; signed: differing signs decide, otherwise the sign of a - b
		ltu = ~sum[X];
		lt  = (a_i[X-1] != b_i[X-1]) ? a_i[X-1] : sum[X-1];

		// SLL = reverse, shift right, reverse back; SRA fills with the sign bit
		for (int i = 0; i < X; i++) rev_in[i] = a_sh[X-1-i];
		sh_in = (op_i == riscv_pkg::ALU_SLL) ? rev_in : a_sh;
		fill  = (op_i == riscv_pkg::ALU_SRA) & a_sh[X-1];
		sh_r  = X'($signed({fill, sh_in}) >>> shamt);
		for (int i = 0; i < X; i++) sh_out[i] = sh_r[X-1-i];

		r_full = '0;

		case (op_i)
			riscv_pkg::ALU_ADD,
			riscv_pkg::ALU_SUB:  r_full = sum[X-1:0];
			riscv_pkg::ALU_AND:  r_full = a_i & b_i;
			riscv_pkg::ALU_OR:   r_full = a_i | b_i;
			riscv_pkg::ALU_XOR:  r_full = a_i ^ b_i;
			riscv_pkg::ALU_SLL:  r_full = sh_out;
			riscv_pkg::ALU_SRL,
			riscv_pkg::ALU_SRA:  r_full = sh_r;
			riscv_pkg::ALU_SLT:  r_full = {{(X-1){1'b0}}, lt};
			riscv_pkg::ALU_SLTU: r_full = {{(X-1){1'b0}}, ltu};
			default:             r_full = '0;
		endcase // op_i
	end

endmodule : alu
