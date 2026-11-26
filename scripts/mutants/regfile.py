# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/common/regfile.sv for tb/unit/test_regfile.py (see scripts/mutate.py)"""

RTL = "rtl/common/regfile.sv"
TEST = "tb/unit/test_regfile.py"

MUTANTS = [
    ("write goes to a neighbouring register", "mem[waddr_i] <= wdata_i;", "mem[waddr_i ^ 5'd1] <= wdata_i;"),
    ("write enable ignored", "if (we_i && (waddr_i != 5'd0)) begin", "if (waddr_i != 5'd0) begin"),
    ("port 2 reads register of port 1", "rdata2_o = mem[raddr2_i];", "rdata2_o = mem[raddr1_i];"),
    ("port 1 reads register of port 2", "rdata1_o = mem[raddr1_i];", "rdata1_o = mem[raddr2_i];"),
    ("no write-through on port 1", "WRITE_THROUGH && we_i && (waddr_i == raddr1_i)", "1'b0"),
    ("no write-through on port 2", "WRITE_THROUGH && we_i && (waddr_i == raddr2_i)", "1'b0"),
    ("write-through without write enable on port 1", "WRITE_THROUGH && we_i && (waddr_i == raddr1_i)", "WRITE_THROUGH && (waddr_i == raddr1_i)"),
    ("write-through without write enable on port 2", "WRITE_THROUGH && we_i && (waddr_i == raddr2_i)", "WRITE_THROUGH && (waddr_i == raddr2_i)"),
    ("write-through on by default off", "parameter bit WRITE_THROUGH = 1'b1", "parameter bit WRITE_THROUGH = 1'b0"),
    ("x0 on port 1 not forced to zero", "if (raddr1_i == 5'd0) begin", "if (1'b0) begin"),
    ("x0 on port 2 not forced to zero", "if (raddr2_i == 5'd0) begin", "if (1'b0) begin"),
    ("write to x0 allowed", "if (we_i && (waddr_i != 5'd0)) begin\n\t\t\tmem[waddr_i] <= wdata_i;",
     "if (we_i) begin\n\t\t\tmem[waddr_i] <= wdata_i;"),
]

EQUIVALENT = {
    "write to x0 allowed": "mem is declared [1:31]: a write to index 0 falls outside the array and is dropped; reads of x0 are forced to 0",
}
