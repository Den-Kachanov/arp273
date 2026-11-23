// SPDX-License-Identifier: CERN-OHL-S-2.0

module imm_gen (
    input  logic [31:0]                inst_i,
    input  riscv_pkg::imm_sel_t        sel_i,
    output logic [riscv_pkg::XLEN-1:0] imm_o
);

    logic unused_opcode;

    assign unused_opcode = &{1'b0, inst_i[6:0]};

    always_comb begin
        imm_o = '0;

        case (sel_i)

            // I-type: 12-bit immediate
            riscv_pkg::IMM_I:
                imm_o = {
                    {(riscv_pkg::XLEN-12){inst_i[31]}},
                    inst_i[31:20]
                };

            // S-type: 12-bit immediate
            riscv_pkg::IMM_S:
                imm_o = {
                    {(riscv_pkg::XLEN-12){inst_i[31]}},
                    inst_i[31:25],
                    inst_i[11:7]
                };

            // B-type: 13-bit immediate
            riscv_pkg::IMM_B:
                imm_o = {
                    {(riscv_pkg::XLEN-13){inst_i[31]}},
                    inst_i[31],
                    inst_i[7],
                    inst_i[30:25],
                    inst_i[11:8],
                    1'b0
                };

            // U-type: 32-bit immediate
            riscv_pkg::IMM_U:
                imm_o = {
                    {(riscv_pkg::XLEN-32){inst_i[31]}},
                    inst_i[31:12],
                    12'b0
                };

            // J-type: 21-bit immediate
            riscv_pkg::IMM_J:
                imm_o = {
                    {(riscv_pkg::XLEN-21){inst_i[31]}},
                    inst_i[31],
                    inst_i[19:12],
                    inst_i[20],
                    inst_i[30:21],
                    1'b0
                };

            default:
                imm_o = '0;

        endcase
    end

endmodule : imm_gen
