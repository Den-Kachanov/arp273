// SPDX-License-Identifier: CERN-OHL-S-2.0

module lsu (
	input  logic [2:0]                      funct3_i,
	input  logic [2:0]                      addr_lo_i,
	input  logic [riscv_pkg::XLEN-1:0]      store_data_i,
	output logic [riscv_pkg::XLEN-1:0]      wdata_o,
	output logic [riscv_pkg::XLEN/8-1:0]    wstrb_o,
	input  logic [riscv_pkg::XLEN-1:0]      rdata_i,
	output logic [riscv_pkg::XLEN-1:0]      load_data_o,
	output logic                            misaligned_o
);

	localparam int STRB = riscv_pkg::XLEN / 8;
	localparam int OFF  = $clog2(STRB);

	logic [OFF-1:0]             offset;
	logic [1:0]                 size;
	logic                       is_unsigned;
	logic [riscv_pkg::XLEN-1:0] shifted;
	logic [STRB-1:0]            size_mask;
	logic                       unused_addr;

	// the memory is XLEN bits wide and aligned: addr_lo_i[OFF-1:0] is the byte inside the word
	assign offset      = addr_lo_i[OFF-1:0];
	assign size        = funct3_i[1:0];
	assign is_unsigned = funct3_i[2];
	assign unused_addr = &{1'b0, addr_lo_i};

	// size: 0 = byte, 1 = half, 2 = word, 3 = double (RV64 only)
	always_comb begin
		size_mask    = '0;
		misaligned_o = 1'b0;

		case (size)
			2'b00: size_mask = STRB'(1'b1);
			2'b01: begin
				size_mask    = STRB'(2'b11);
				misaligned_o = offset[0];
			end
			2'b10: begin
				size_mask    = STRB'(4'b1111);
				misaligned_o = (offset[OFF-1:0] & OFF'(3)) != '0;
			end
			default: begin
				size_mask    = '1;
				misaligned_o = (offset != '0);
			end
		endcase // size
	end

	// store: move the data to its byte lane; only the bytes in wstrb_o are written
	assign wdata_o = store_data_i << {offset, 3'b000};
	assign wstrb_o = size_mask << offset;

	// load: move the byte lane to bit 0, then extend by size and signedness
	assign shifted = rdata_i >> {offset, 3'b000};

	always_comb begin
		load_data_o = shifted;

		case (size)
			2'b00:   load_data_o = {{(riscv_pkg::XLEN-8){!is_unsigned & shifted[7]}}, shifted[7:0]};
			2'b01:   load_data_o = {{(riscv_pkg::XLEN-16){!is_unsigned & shifted[15]}}, shifted[15:0]};
			2'b10:   load_data_o = {{(riscv_pkg::XLEN-32){!is_unsigned & shifted[31]}}, shifted[31:0]};
			default: load_data_o = shifted;
		endcase // size
	end

endmodule : lsu
