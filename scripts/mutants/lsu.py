# SPDX-License-Identifier: CERN-OHL-S-2.0
"""Mutants of rtl/core/lsu.sv for tb/unit/test_lsu.py (see scripts/mutate.py)"""

RTL = "rtl/core/lsu.sv"
TEST = "tb/unit/test_lsu.py"

MUTANTS = [
    ("halfword alignment ignored", "misaligned_o = offset[0];", "misaligned_o = 1'b0;"),
    ("word alignment checks only bit 0", "misaligned_o = (offset[OFF-1:0] & OFF'(3)) != '0;", "misaligned_o = (offset[OFF-1:0] & OFF'(1)) != '0;"),
    ("doubleword alignment checks fewer bits", "misaligned_o = (offset != '0);", "misaligned_o = (offset[OFF-1:1] != '0);"),
    ("store strobe not shifted", "assign wstrb_o = size_mask << offset;", "assign wstrb_o = size_mask;"),
    ("store data not shifted", "assign wdata_o = store_data_i << {offset, 3'b000};", "assign wdata_o = store_data_i;"),
    ("load data not shifted", "assign shifted = rdata_i >> {offset, 3'b000};", "assign shifted = rdata_i;"),
    ("LB sign from bit 15", "!is_unsigned & shifted[7]}}", "!is_unsigned & shifted[15]}}"),
    ("LH always zero-extended", "{(riscv_pkg::XLEN-16){!is_unsigned & shifted[15]}}", "{(riscv_pkg::XLEN-16){1'b0}}"),
    ("LW always zero-extended", "{(riscv_pkg::XLEN-32){!is_unsigned & shifted[31]}}", "{(riscv_pkg::XLEN-32){1'b0}}"),
    ("halfword strobe has one byte", "size_mask    = STRB'(2'b11);", "size_mask    = STRB'(2'b01);"),
    ("unsigned flag ignored", "assign is_unsigned = funct3_i[2];", "assign is_unsigned = 1'b0;"),
]

EQUIVALENT = {}
