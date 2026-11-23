// SPDX-License-Identifier: CERN-OHL-S-2.0

module alu (
	input  logic [riscv_pkg::XLEN-1:0] a_i,
	input  logic [riscv_pkg::XLEN-1:0] b_i,
	input  riscv_pkg::alu_op_t         op_i,
	input  logic					   word_i,
	output logic [riscv_pkg::XLEN-1:0] r_o
);

	localparam int SHW = $clog2(riscv_pkg::XLEN);

	logic [riscv_pkg::XLEN-1:0] a_sh;
	logic [SHW-1:0]             shamt;
	logic [riscv_pkg::XLEN-1:0] r_full;

	if (riscv_pkg::XLEN == 64) begin : g_rv64
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


	always_comb begin
		r_full = '0;

		case (op_i)
			riscv_pkg::ALU_ADD:  r_full = a_i + b_i;
			riscv_pkg::ALU_SUB:  r_full = a_i - b_i;
			riscv_pkg::ALU_AND:  r_full = a_i & b_i;
			riscv_pkg::ALU_OR:   r_full = a_i | b_i;
			riscv_pkg::ALU_XOR:  r_full = a_i ^ b_i;
			riscv_pkg::ALU_SLL:  r_full = a_sh << shamt;
			riscv_pkg::ALU_SRL:  r_full = a_sh >> shamt;
			riscv_pkg::ALU_SRA:  r_full = $signed(a_sh) >>> shamt;
			riscv_pkg::ALU_SLT:  r_full = {{(riscv_pkg::XLEN-1){1'b0}}, $signed(a_i) < $signed(b_i)};
			riscv_pkg::ALU_SLTU: r_full = {{(riscv_pkg::XLEN-1){1'b0}}, a_i < b_i};
			default:             r_full = '0;
		endcase // op_i
	end

endmodule : alu
