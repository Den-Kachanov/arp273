// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

// Machine-mode CSRs and the exception state of the pipelined core (ADR 014). Only M-mode exists
//
// Access: in the WB stage, for a committing CSR instruction. rdata_o is the old value (it goes to rd);
// illegal_o is set for a CSR that does not exist, or for a write to a read-only CSR
// (address bits [11:10] = 11); then nothing is written and the core takes an illegal-instruction trap.
// Write ops (funct3[1:0]): 01 write, 10 set bits, 11 clear bits; write_i is 0 for CSRRS/CSRRC with
// rs1 = x0 (or a zero immediate), which then only read
//
// Implemented:
//   mstatus  0x300  MIE (bit 3), MPIE (bit 7); MPP (12:11) reads 11 (only M-mode); other bits read 0
//   misa     0x301  read-only value: MXL and the I, M and A extensions; writes are ignored (WARL)
//   mie      0x304  MSIE, MTIE, MEIE writable
//   mtvec    0x305  direct mode only: bits 1:0 read 0
//   mscratch 0x340
//   mepc     0x341  bits 1:0 read 0 (no C extension)
//   mcause   0x342
//   mtval    0x343
//   mip      0x344  MSIP, MTIP, MEIP: the interrupt lines (CLINT, PLIC), registered here; writes ignored
//   mcycle 0xB00, minstret 0xB02 (and mcycleh/minstreth for XLEN = 32): read the performance
//   counters; writes are ignored (a simplification, ADR 014)
//   cycle 0xC00, instret 0xC02 (and cycleh/instreth): read-only copies
//   mvendorid 0xF11, marchid 0xF12, mimpid 0xF13, mconfigptr 0xF15: read 0; mhartid 0xF14: HART_ID
// Anything else (satp, pmp*, medeleg, mideleg, stvec...) does not exist: illegal instruction
//
// Trap entry (trap_i): mepc <= epc_i, mcause <= cause_i, mtval <= tval_i, MPIE <= MIE, MIE <= 0.
// MRET (mret_i): MIE <= MPIE, MPIE <= 1
//
// Interrupts (ADR 019): irq_i = {MEIP, MTIP, MSIP} are levels from the CLINT and the PLIC, registered
// once here so that the decision in WB starts from flip-flops. irq_pending_o: an interrupt is pending
// and enabled in mie (this wakes WFI whatever MIE is); irq_take_o: also MIE is set, the core takes it.
// irq_code_o: the highest priority one, MEI (11) > MSI (3) > MTI (7), as the specification orders them

module csr #(
	// this hart's number, read from mhartid (ADR 019: several harts share the memory)
	parameter int HART_ID = 0
) (
	input  logic                       clk_i,
	input  logic                       rst_ni,

	input  logic                       access_i,
	input  logic [11:0]                addr_i,
	input  logic [1:0]                 op_i,
	input  logic                       write_i,
	input  logic [riscv_pkg::XLEN-1:0] wdata_i,
	output logic [riscv_pkg::XLEN-1:0] rdata_o,
	output logic                       illegal_o,

	input  logic                       trap_i,
	input  logic [riscv_pkg::XLEN-1:0] cause_i,
	input  logic [riscv_pkg::XLEN-1:0] epc_i,
	input  logic [riscv_pkg::XLEN-1:0] tval_i,
	input  logic                       mret_i,

	output logic [riscv_pkg::XLEN-1:0] mtvec_o,
	output logic [riscv_pkg::XLEN-1:0] mepc_o,

	input  logic [2:0]                 irq_i,
	output logic                       irq_pending_o,
	output logic                       irq_take_o,
	output logic [3:0]                 irq_code_o,

	input  logic [63:0]                cycle_i,
	input  logic [63:0]                instret_i
);

	localparam int X = riscv_pkg::XLEN;

	// misa: MXL in the two top bits (1 = 32, 2 = 64), extension bits A (0), I (8) and M (12)
	localparam logic [X-1:0] MISA = {(X == 64) ? 2'b10 : 2'b01, {(X - 28){1'b0}}, 26'h0001101};

	logic         mie_q, mpie_q;
	logic [2:0]   mie_bits_q;                  // MEIE, MTIE, MSIE
	logic [2:0]   irq_q;                       // MEIP, MTIP, MSIP
	logic [2:0]   irq_en;
	logic [X-1:0] mip_reg;
	logic [X-1:0] mtvec_q, mscratch_q, mepc_q, mcause_q, mtval_q;

	logic [X-1:0] mstatus, mie_reg;
	logic [X-1:0] old;
	logic         exists;
	logic         writable;
	logic [X-1:0] new_val;
	logic         do_write;

	always_comb begin
		mstatus     = '0;
		mstatus[3]  = mie_q;
		mstatus[7]  = mpie_q;
		mstatus[12] = 1'b1;
		mstatus[11] = 1'b1;
		mie_reg     = '0;
		mie_reg[3]  = mie_bits_q[0];
		mie_reg[7]  = mie_bits_q[1];
		mie_reg[11] = mie_bits_q[2];
		mip_reg     = '0;
		mip_reg[3]  = irq_q[0];
		mip_reg[7]  = irq_q[1];
		mip_reg[11] = irq_q[2];
	end

	assign irq_en        = irq_q & mie_bits_q;
	assign irq_pending_o = |irq_en;
	assign irq_take_o    = mie_q & irq_pending_o;
	assign irq_code_o    = irq_en[2] ? 4'd11 : (irq_en[0] ? 4'd3 : 4'd7);

	// read mux and existence
	always_comb begin
		old    = '0;
		exists = 1'b1;
		case (addr_i)
			12'h300: old = mstatus;
			12'h301: old = MISA;
			12'h304: old = mie_reg;
			12'h305: old = mtvec_q;
			12'h340: old = mscratch_q;
			12'h341: old = mepc_q;
			12'h342: old = mcause_q;
			12'h343: old = mtval_q;
			12'h344: old = mip_reg;
			12'hB00, 12'hC00: old = cycle_i[X-1:0];
			12'hB02, 12'hC02: old = instret_i[X-1:0];
			12'hF11, 12'hF12, 12'hF13, 12'hF15: old = '0;
			12'hF14: old = X'(HART_ID);
			default: begin
				exists = 1'b0;
				// the high halves of the counters exist only for XLEN = 32
				if (X == 32) begin
					case (addr_i)
						12'hB80, 12'hC80: begin old = X'(cycle_i >> 32);   exists = 1'b1; end
						12'hB82, 12'hC82: begin old = X'(instret_i >> 32); exists = 1'b1; end
						default: begin end
					endcase
				end
			end
		endcase
	end

	assign writable  = (addr_i[11:10] != 2'b11);
	assign illegal_o = ~exists | (write_i & ~writable);
	assign rdata_o   = old;

	always_comb begin
		case (op_i)
			2'b01:   new_val = wdata_i;
			2'b10:   new_val = old | wdata_i;
			2'b11:   new_val = old & ~wdata_i;
			default: new_val = old;
		endcase
	end

	assign do_write = access_i & write_i & ~illegal_o & ~trap_i;

	always_ff @(posedge clk_i) begin
		if (!rst_ni) irq_q <= '0;
		else         irq_q <= irq_i;
	end

	always_ff @(posedge clk_i) begin
		if (!rst_ni) begin
			mie_q      <= 1'b0;
			mpie_q     <= 1'b0;
			mie_bits_q <= '0;
			mtvec_q    <= '0;
			mcause_q   <= '0;
		end else if (trap_i) begin
			mepc_q   <= epc_i;                // instruction addresses are 4-byte aligned
			mcause_q <= cause_i;
			mtval_q  <= tval_i;
			mpie_q   <= mie_q;
			mie_q    <= 1'b0;
		end else if (mret_i) begin
			mie_q  <= mpie_q;
			mpie_q <= 1'b1;
		end else if (do_write) begin
			case (addr_i)
				12'h300: begin
					mie_q  <= new_val[3];
					mpie_q <= new_val[7];
				end
				12'h304: mie_bits_q <= {new_val[11], new_val[7], new_val[3]};
				12'h305: mtvec_q    <= {new_val[X-1:2], 2'b00};
				12'h340: mscratch_q <= new_val;
				12'h341: mepc_q     <= {new_val[X-1:2], 2'b00};
				12'h342: mcause_q   <= new_val;
				12'h343: mtval_q    <= new_val;
				default: begin end           // misa, mip, counters: writes ignored
			endcase
		end
	end

	assign mtvec_o = mtvec_q;
	assign mepc_o  = mepc_q;

endmodule : csr
