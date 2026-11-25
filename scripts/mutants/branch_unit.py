# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/core/branch_unit.sv for tb/unit/test_branch_unit.py (see scripts/mutate.py)"""

RTL = "rtl/core/branch_unit.sv"
TEST = "tb/unit/test_branch_unit.py"

MUTANTS = [
    ("BEQ is BNE", "taken_o = (a_i == b_i);", "taken_o = (a_i != b_i);"),
    ("BNE is BEQ", "taken_o = (a_i != b_i);", "taken_o = (a_i == b_i);"),
    ("BLT is unsigned", "taken_o = ($signed(a_i) <  $signed(b_i));", "taken_o = (a_i < b_i);"),
    ("BGE is unsigned", "taken_o = ($signed(a_i) >= $signed(b_i));", "taken_o = (a_i >= b_i);"),
    ("BLTU is signed", "taken_o = (a_i <  b_i);", "taken_o = ($signed(a_i) < $signed(b_i));"),
    ("BGEU is signed", "taken_o = (a_i >= b_i);", "taken_o = ($signed(a_i) >= $signed(b_i));"),
    ("BLT is not strict", "taken_o = ($signed(a_i) <  $signed(b_i));", "taken_o = ($signed(a_i) <= $signed(b_i));"),
    ("reserved funct3 takes the branch", "default: taken_o = 1'b0;", "default: taken_o = 1'b1;"),
]

EQUIVALENT = {}
