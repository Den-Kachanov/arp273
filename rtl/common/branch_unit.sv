// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

module branch_unit (
	input  logic [riscv_pkg::XLEN-1:0] a_i,
	input  logic [riscv_pkg::XLEN-1:0] b_i,
	input  logic [2:0]                 funct3_i,
	output logic                       taken_o
);

	// the ALU is busy computing the target pc + imm, so the comparison needs its own comparators
	always_comb begin
		taken_o = 1'b0;

		case (funct3_i)
			3'b000:  taken_o = (a_i == b_i);                    // BEQ
			3'b001:  taken_o = (a_i != b_i);                    // BNE
			3'b100:  taken_o = ($signed(a_i) <  $signed(b_i));  // BLT
			3'b101:  taken_o = ($signed(a_i) >= $signed(b_i));  // BGE
			3'b110:  taken_o = (a_i <  b_i);                    // BLTU
			3'b111:  taken_o = (a_i >= b_i);                    // BGEU
			default: taken_o = 1'b0;
		endcase // funct3_i
	end

endmodule : branch_unit
