// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov

module decoder (
	input  logic [31:0]         inst_i,
	output riscv_pkg::alu_op_t  alu_op_o,
	output logic                word_o,
	output riscv_pkg::imm_sel_t imm_sel_o,
	output riscv_pkg::a_sel_t   a_sel_o,
	output riscv_pkg::b_sel_t   b_sel_o,
	output riscv_pkg::wb_sel_t  wb_sel_o,
	output logic                reg_we_o,
	output logic                mem_re_o,
	output logic                mem_we_o,
	output logic                branch_o,
	output logic                jal_o,
	output logic                jalr_o,
	output logic                muldiv_o,
	// SYSTEM and MISC-MEM (ADR 014): CSRRW/S/C and the immediate forms, ECALL, EBREAK, MRET, WFI, FENCE.I
	output logic                csr_o,
	output logic                ecall_o,
	output logic                ebreak_o,
	output logic                mret_o,
	output logic                wfi_o,
	output logic                fence_i_o,
	// A extension (ADR 019): LR, SC and the AMOs. The address is rs1 (no offset); mem_re is set for
	// all of them (rd gets the old value, or the SC result), mem_we for all but LR; the operation is
	// inst[31:27], sent on the data port. aq and rl (inst[26:25]) need nothing in an in-order core with
	// one data request in flight: every access is already ordered
	output logic                amo_o,
	output logic [2:0]          funct3_o,
	output logic                illegal_o
);

	localparam bit RV64 = (riscv_pkg::XLEN == 64);

	logic [2:0] funct3;
	logic [6:0] funct7;
	logic       shift_hi_zero;
	logic       shift_hi_sra;
	logic       unused_fields;

	assign funct3   = inst_i[14:12];
	assign funct7   = inst_i[31:25];
	assign funct3_o = funct3;

	// shift immediates: inst[31:26] selects the operation, inst[25] is shamt[5] (RV64 only)
	assign shift_hi_zero = (inst_i[31:26] == 6'b000000) && (RV64 || !inst_i[25]);
	assign shift_hi_sra  = (inst_i[31:26] == 6'b010000) && (RV64 || !inst_i[25]);

	// rd, rs1, rs2 are not decoded here: the datapath reads them straight from inst
	assign unused_fields = &{1'b0, inst_i[24:15], inst_i[11:7]};

	always_comb begin
		alu_op_o  = riscv_pkg::ALU_ADD;
		word_o    = 1'b0;
		imm_sel_o = riscv_pkg::IMM_I;
		a_sel_o   = riscv_pkg::A_RS1;
		b_sel_o   = riscv_pkg::B_RS2;
		wb_sel_o  = riscv_pkg::WB_ALU;
		reg_we_o  = 1'b0;
		mem_re_o  = 1'b0;
		mem_we_o  = 1'b0;
		branch_o  = 1'b0;
		jal_o     = 1'b0;
		jalr_o    = 1'b0;
		muldiv_o  = 1'b0;
		csr_o     = 1'b0;
		ecall_o   = 1'b0;
		ebreak_o  = 1'b0;
		mret_o    = 1'b0;
		wfi_o     = 1'b0;
		fence_i_o = 1'b0;
		amo_o     = 1'b0;
		illegal_o = 1'b1;

		case (inst_i[6:0])
			// R-type: op = {funct7[5], funct3}; funct7 is 0, or 0100000 for SUB and SRA.
			// funct7 = 0000001 is the M extension: MUL, MULH, MULHSU, MULHU, DIV, DIVU, REM, REMU;
			// the ALU is not used, the result comes from the multiplier/divider
			riscv_pkg::OPC_OP: begin
				reg_we_o = 1'b1;
				alu_op_o = riscv_pkg::alu_op_t'({funct7[5], funct3});
				if (funct7 == 7'b0000000 ||
				    (funct7 == 7'b0100000 && (funct3 == 3'b000 || funct3 == 3'b101)))
					illegal_o = 1'b0;
				if (funct7 == 7'b0000001) begin
					alu_op_o  = riscv_pkg::ALU_ADD;
					wb_sel_o  = riscv_pkg::WB_MD;
					muldiv_o  = 1'b1;
					illegal_o = 1'b0;
				end
			end

			// RV64 only: ADDW, SUBW, SLLW, SRLW, SRAW; with funct7 = 0000001: MULW, DIVW, DIVUW, REMW, REMUW
			riscv_pkg::OPC_OP_32: begin
				reg_we_o = 1'b1;
				word_o   = 1'b1;
				alu_op_o = riscv_pkg::alu_op_t'({funct7[5], funct3});
				if (RV64 && (
				    (funct3 == 3'b000 && (funct7 == 7'b0000000 || funct7 == 7'b0100000)) ||
				    (funct3 == 3'b001 && funct7 == 7'b0000000) ||
				    (funct3 == 3'b101 && (funct7 == 7'b0000000 || funct7 == 7'b0100000))))
					illegal_o = 1'b0;
				if (RV64 && funct7 == 7'b0000001 &&
				    (funct3 == 3'b000 || funct3[2])) begin
					alu_op_o  = riscv_pkg::ALU_ADD;
					wb_sel_o  = riscv_pkg::WB_MD;
					muldiv_o  = 1'b1;
					illegal_o = 1'b0;
				end
			end

			// I-type ALU: inst[30] belongs to the immediate, so the top bit of op is
			// cleared, except for SRAI where it selects SRA instead of SRL
			riscv_pkg::OPC_OP_IMM: begin
				reg_we_o  = 1'b1;
				imm_sel_o = riscv_pkg::IMM_I;
				b_sel_o   = riscv_pkg::B_IMM;
				alu_op_o  = riscv_pkg::alu_op_t'({1'b0, funct3});
				case (funct3)
					3'b001: illegal_o = !shift_hi_zero;
					3'b101: begin
						if (shift_hi_sra) alu_op_o = riscv_pkg::ALU_SRA;
						illegal_o = !(shift_hi_zero || shift_hi_sra);
					end
					default: illegal_o = 1'b0;
				endcase
			end

			// RV64 only: ADDIW, SLLIW, SRLIW, SRAIW
			riscv_pkg::OPC_OP_IMM_32: begin
				reg_we_o  = 1'b1;
				word_o    = 1'b1;
				imm_sel_o = riscv_pkg::IMM_I;
				b_sel_o   = riscv_pkg::B_IMM;
				alu_op_o  = riscv_pkg::alu_op_t'({1'b0, funct3});
				case (funct3)
					3'b000: illegal_o = !RV64;
					3'b001: illegal_o = !(RV64 && funct7 == 7'b0000000);
					3'b101: begin
						if (funct7 == 7'b0100000) alu_op_o = riscv_pkg::ALU_SRA;
						illegal_o = !(RV64 && (funct7 == 7'b0000000 || funct7 == 7'b0100000));
					end
					default: illegal_o = 1'b1;
				endcase
			end

			// LUI: 0 + imm
			riscv_pkg::OPC_LUI: begin
				reg_we_o  = 1'b1;
				imm_sel_o = riscv_pkg::IMM_U;
				a_sel_o   = riscv_pkg::A_ZERO;
				b_sel_o   = riscv_pkg::B_IMM;
				illegal_o = 1'b0;
			end

			// AUIPC: pc + imm
			riscv_pkg::OPC_AUIPC: begin
				reg_we_o  = 1'b1;
				imm_sel_o = riscv_pkg::IMM_U;
				a_sel_o   = riscv_pkg::A_PC;
				b_sel_o   = riscv_pkg::B_IMM;
				illegal_o = 1'b0;
			end

			// LB, LH, LW, LBU, LHU; LD and LWU only in RV64; address = rs1 + imm
			riscv_pkg::OPC_LOAD: begin
				reg_we_o  = 1'b1;
				mem_re_o  = 1'b1;
				wb_sel_o  = riscv_pkg::WB_MEM;
				imm_sel_o = riscv_pkg::IMM_I;
				b_sel_o   = riscv_pkg::B_IMM;
				case (funct3)
					3'b000, 3'b001, 3'b010, 3'b100, 3'b101: illegal_o = 1'b0;
					3'b011, 3'b110:                         illegal_o = !RV64;
					default:                                illegal_o = 1'b1;
				endcase
			end

			// SB, SH, SW; SD only in RV64; address = rs1 + imm
			riscv_pkg::OPC_STORE: begin
				mem_we_o  = 1'b1;
				imm_sel_o = riscv_pkg::IMM_S;
				b_sel_o   = riscv_pkg::B_IMM;
				case (funct3)
					3'b000, 3'b001, 3'b010: illegal_o = 1'b0;
					3'b011:                 illegal_o = !RV64;
					default:                illegal_o = 1'b1;
				endcase
			end

			// the ALU computes the target pc + imm; the comparison is done by the branch unit
			riscv_pkg::OPC_BRANCH: begin
				branch_o  = 1'b1;
				imm_sel_o = riscv_pkg::IMM_B;
				a_sel_o   = riscv_pkg::A_PC;
				b_sel_o   = riscv_pkg::B_IMM;
				illegal_o = (funct3 == 3'b010 || funct3 == 3'b011);
			end

			// target pc + imm, rd = pc + 4
			riscv_pkg::OPC_JAL: begin
				reg_we_o  = 1'b1;
				jal_o     = 1'b1;
				wb_sel_o  = riscv_pkg::WB_PC4;
				imm_sel_o = riscv_pkg::IMM_J;
				a_sel_o   = riscv_pkg::A_PC;
				b_sel_o   = riscv_pkg::B_IMM;
				illegal_o = 1'b0;
			end

			// target rs1 + imm, rd = pc + 4
			riscv_pkg::OPC_JALR: begin
				reg_we_o  = 1'b1;
				jalr_o    = 1'b1;
				wb_sel_o  = riscv_pkg::WB_PC4;
				imm_sel_o = riscv_pkg::IMM_I;
				b_sel_o   = riscv_pkg::B_IMM;
				illegal_o = (funct3 != 3'b000);
			end

			// LR.W/D, SC.W/D, AMO*.W/D; .D only in RV64; LR needs rs2 = 0
			riscv_pkg::OPC_AMO: begin
				reg_we_o = 1'b1;
				mem_re_o = 1'b1;
				amo_o    = 1'b1;
				wb_sel_o = riscv_pkg::WB_MEM;
				mem_we_o = (inst_i[31:27] != riscv_pkg::AMO_LR);
				b_sel_o  = (inst_i[31:27] == riscv_pkg::AMO_LR) ? riscv_pkg::B_IMM : riscv_pkg::B_RS2;
				case (inst_i[31:27])
					riscv_pkg::AMO_LR:  illegal_o = (inst_i[24:20] != 5'd0);
					riscv_pkg::AMO_ADD, riscv_pkg::AMO_SWAP, riscv_pkg::AMO_SC, riscv_pkg::AMO_XOR,
					riscv_pkg::AMO_OR, riscv_pkg::AMO_AND, riscv_pkg::AMO_MIN, riscv_pkg::AMO_MAX,
					riscv_pkg::AMO_MINU, riscv_pkg::AMO_MAXU: illegal_o = 1'b0;
					default:            illegal_o = 1'b1;
				endcase
				if (!(funct3 == 3'b010 || (RV64 && funct3 == 3'b011)))
					illegal_o = 1'b1;
			end

			// FENCE is a no-op in a single-hart core without caches. FENCE.I makes stores visible to
			// instruction fetch: the core flushes what it has fetched ahead; its imm, rs1 and rd fields
			// are reserved and ignored, as the specification asks
			riscv_pkg::OPC_MISC_MEM: begin
				case (funct3)
					3'b000:  illegal_o = 1'b0;
					3'b001: begin
						fence_i_o = 1'b1;
						illegal_o = 1'b0;
					end
					default: illegal_o = 1'b1;
				endcase
			end

			// SYSTEM. funct3 = 0: ECALL, EBREAK, MRET, WFI (waits for an interrupt, ADR 019), all with fixed
			// encodings. Otherwise a CSR instruction: rd gets the old value; the register forms read
			// rs1, the immediate forms (funct3[2] = 1) take rs1 as a 5-bit zero-extended immediate.
			// Whether the CSR exists and may be written is decided by the CSR unit
			riscv_pkg::OPC_SYSTEM: begin
				b_sel_o = riscv_pkg::B_IMM;
				case (funct3)
					3'b000: begin
						case (inst_i)
							32'h0000_0073: begin ecall_o  = 1'b1; illegal_o = 1'b0; end
							32'h0010_0073: begin ebreak_o = 1'b1; illegal_o = 1'b0; end
							32'h3020_0073: begin mret_o   = 1'b1; illegal_o = 1'b0; end
							32'h1050_0073: begin wfi_o    = 1'b1; illegal_o = 1'b0; end
							default:       illegal_o = 1'b1;
						endcase
					end
					3'b100:  illegal_o = 1'b1;
					default: begin
						csr_o     = 1'b1;
						reg_we_o  = 1'b1;
						a_sel_o   = funct3[2] ? riscv_pkg::A_ZERO : riscv_pkg::A_RS1;
						illegal_o = 1'b0;
					end
				endcase
			end

			default: illegal_o = 1'b1;
		endcase // inst_i[6:0]

		// an illegal instruction must have no side effects
		if (illegal_o) begin
			reg_we_o = 1'b0;
			mem_re_o = 1'b0;
			mem_we_o = 1'b0;
			branch_o = 1'b0;
			jal_o    = 1'b0;
			jalr_o   = 1'b0;
			muldiv_o = 1'b0;
			csr_o    = 1'b0;
			amo_o    = 1'b0;
		end
	end

endmodule : decoder
