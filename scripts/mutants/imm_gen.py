# SPDX-License-Identifier: CERN-OHL-S-2.0
"""Mutants of rtl/core/imm_gen.sv for tb/unit/test_imm_gen.py (see scripts/mutate.py)

Only changes that keep the result width are used: a mutant that does not build proves nothing
"""

RTL = "rtl/core/imm_gen.sv"
TEST = "tb/unit/test_imm_gen.py"

MUTANTS = [
    # I-type
    ("I: wrong field", "inst_i[31:20]", "inst_i[30:19]"),
    # S-type
    ("S: low part from rd shifted", "inst_i[11:7]", "inst_i[10:6]"),
    ("S: high part shifted", "inst_i[31:25],", "inst_i[30:24],"),
    # B-type
    ("B: bit 11 from the wrong bit", "inst_i[7],", "inst_i[8],"),
    ("B: low field shifted", "inst_i[11:8],", "inst_i[10:7],"),
    ("B: middle field shifted", "inst_i[30:25],", "inst_i[29:24],"),
    ("B: sign extension from bit 30", "{(riscv_pkg::XLEN-13){inst_i[31]}}", "{(riscv_pkg::XLEN-13){inst_i[30]}}"),
    # U-type
    ("U: field shifted", "inst_i[31:12],", "inst_i[30:11],"),
    ("U: low bits not zero", "12'b0", "12'b1"),
    ("U: sign extension from bit 30", "{(riscv_pkg::XLEN-32){inst_i[31]}}", "{(riscv_pkg::XLEN-32){inst_i[30]}}"),
    # J-type
    ("J: bits 19:12 shifted", "inst_i[19:12],", "inst_i[18:11],"),
    ("J: bit 11 from the wrong bit", "inst_i[20],", "inst_i[21],"),
    ("J: low field shifted", "inst_i[30:21],", "inst_i[29:20],"),
    ("J: sign extension from bit 30", "{(riscv_pkg::XLEN-21){inst_i[31]}}", "{(riscv_pkg::XLEN-21){inst_i[30]}}"),
]

EQUIVALENT = {}
