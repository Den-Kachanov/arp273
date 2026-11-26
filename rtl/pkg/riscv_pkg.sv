// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

`ifndef XLEN
  `define XLEN 64
`endif

package riscv_pkg;

	parameter int XLEN = `XLEN;

	typedef enum logic [6:0] {
		OPC_OP        = 7'b0110011,
		OPC_OP_IMM    = 7'b0010011,
		OPC_OP_32     = 7'b0111011,
		OPC_OP_IMM_32 = 7'b0011011,
		OPC_LUI       = 7'b0110111,
		OPC_AUIPC     = 7'b0010111,
		OPC_LOAD      = 7'b0000011,
		OPC_STORE     = 7'b0100011,
		OPC_BRANCH    = 7'b1100011,
		OPC_JAL       = 7'b1101111,
		OPC_JALR      = 7'b1100111,
		OPC_MISC_MEM  = 7'b0001111,
		OPC_AMO       = 7'b0101111,
		OPC_SYSTEM    = 7'b1110011
	} opcode_t;

	typedef enum logic [3:0] {
		ALU_ADD  = 4'b0000,
		ALU_SLL  = 4'b0001,
		ALU_SLT  = 4'b0010,
		ALU_SLTU = 4'b0011,
		ALU_XOR  = 4'b0100,
		ALU_SRL  = 4'b0101,
		ALU_OR   = 4'b0110,
		ALU_AND  = 4'b0111,
		ALU_SUB  = 4'b1000,
		ALU_SRA  = 4'b1101
	} alu_op_t;

	typedef enum logic [2:0] {
		IMM_I = 3'b000,
		IMM_S = 3'b001,
		IMM_B = 3'b010,
		IMM_U = 3'b011,
		IMM_J = 3'b100
	} imm_sel_t;

	typedef enum logic [1:0] {
		A_RS1  = 2'b00,
		A_PC   = 2'b01,
		A_ZERO = 2'b10
	} a_sel_t;

	typedef enum logic {
		B_RS2 = 1'b0,
		B_IMM = 1'b1
	} b_sel_t;

	typedef enum logic [1:0] {
		WB_ALU = 2'b00,
		WB_MEM = 2'b01,
		WB_PC4 = 2'b10,
		WB_MD  = 2'b11
	} wb_sel_t;

	// atomic operation on the data port (ADR 019): funct5 of the A extension, sent as it is; the
	// memory side (the model, later L2) executes it. Only meaningful with the request's amo bit set
	typedef enum logic [4:0] {
		AMO_ADD  = 5'b00000,
		AMO_SWAP = 5'b00001,
		AMO_LR   = 5'b00010,
		AMO_SC   = 5'b00011,
		AMO_XOR  = 5'b00100,
		AMO_OR   = 5'b01000,
		AMO_AND  = 5'b01100,
		AMO_MIN  = 5'b10000,
		AMO_MAX  = 5'b10100,
		AMO_MINU = 5'b11000,
		AMO_MAXU = 5'b11100
	} amo_op_t;

endpackage
