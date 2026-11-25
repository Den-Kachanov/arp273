// SPDX-License-Identifier: CERN-OHL-S-2.0

module regfile #(
	// 1: a read of the register being written returns wdata_i (pipeline, ADR 004)
	// 0: it returns the old value (single-cycle core, where the write belongs to the same instruction)
	parameter bit WRITE_THROUGH = 1'b1
) (
	input  logic                       clk_i,
	input  logic                       we_i,
	input  logic [4:0]                 waddr_i,
	input  logic [riscv_pkg::XLEN-1:0] wdata_i,
	input  logic [4:0]                 raddr1_i,
	input  logic [4:0]                 raddr2_i,
	output logic [riscv_pkg::XLEN-1:0] rdata1_o,
	output logic [riscv_pkg::XLEN-1:0] rdata2_o
);

	logic [riscv_pkg::XLEN-1:0] mem [1:31];

	// Write port
	always_ff @(posedge clk_i) begin
		if (we_i && (waddr_i != 5'd0)) begin
			mem[waddr_i] <= wdata_i;
		end
	end

	// Read ports
	always_comb begin
		rdata1_o = '0;
		rdata2_o = '0;

		// Read port 1
		if (raddr1_i == 5'd0) begin
			rdata1_o = '0;
		end
		else if (WRITE_THROUGH && we_i && (waddr_i == raddr1_i)) begin
			rdata1_o = wdata_i;
		end
		else begin
			rdata1_o = mem[raddr1_i];
		end

		// Read port 2
		if (raddr2_i == 5'd0) begin
			rdata2_o = '0;
		end
		else if (WRITE_THROUGH && we_i && (waddr_i == raddr2_i)) begin
			rdata2_o = wdata_i;
		end
		else begin
			rdata2_o = mem[raddr2_i];
		end
	end

endmodule : regfile
