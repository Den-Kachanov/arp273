// SPDX-License-Identifier: CERN-OHL-S-2.0

module perf_counters (
	input  logic        clk_i,
	input  logic        rst_ni,
	input  logic        commit_i,
	input  logic        stall_i,
	input  logic        flush_i,
	output logic [63:0] cycles_o,
	output logic [63:0] instret_o,
	output logic [63:0] stalls_o,
	output logic [63:0] flushes_o
);

	logic [63:0] cycles_q;
	logic [63:0] instret_q;
	logic [63:0] stalls_q;
	logic [63:0] flushes_q;

	always_ff @(posedge clk_i) begin
		if (!rst_ni) begin
			cycles_q  <= '0;
			instret_q <= '0;
			stalls_q  <= '0;
			flushes_q <= '0;
		end else begin
			cycles_q  <= cycles_q + 64'd1;
			instret_q <= instret_q + {63'd0, commit_i};
			stalls_q  <= stalls_q + {63'd0, stall_i};
			flushes_q <= flushes_q + {63'd0, flush_i};
		end
	end

	assign cycles_o  = cycles_q;
	assign instret_o = instret_q;
	assign stalls_o  = stalls_q;
	assign flushes_o = flushes_q;

endmodule : perf_counters
