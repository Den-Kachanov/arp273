# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/soc/amo_unit.sv for tb/unit/test_amo_unit.py (see scripts/mutate.py)"""

RTL = "rtl/soc/amo_unit.sv"
TEST = ["tb/unit/test_amo_unit.py"]

MUTANTS = [
    ("SC ignores the address", "assign sc_ok     = resv_valid_q[req_id_i] & (resv_addr_q[req_id_i] == byte_addr);",
     "assign sc_ok     = resv_valid_q[req_id_i];"),
    ("SC ignores the reservation", "assign sc_ok     = resv_valid_q[req_id_i] & (resv_addr_q[req_id_i] == byte_addr);",
     "assign sc_ok     = (resv_addr_q[req_id_i] == byte_addr);"),
    ("own store clears the reservation", "if (IW'(h) != req_id_i && resv_addr_q[h][X-1:SB] == req_addr_i[X-1:SB])",
     "if (resv_addr_q[h][X-1:SB] == req_addr_i[X-1:SB])"),
    ("a store anywhere clears", "if (IW'(h) != req_id_i && resv_addr_q[h][X-1:SB] == req_addr_i[X-1:SB])",
     "if (IW'(h) != req_id_i)"),
    ("stores do not clear reservations", "\t\t\t\tif (writes)\n", "\t\t\t\tif (1'b0)\n"),
    ("an AMO does not clear reservations", "assign writes    = (req_we_i & ~req_amo_i) | (is_sc & sc_ok) | is_amo;",
     "assign writes    = (req_we_i & ~req_amo_i) | (is_sc & sc_ok);"),
    ("a successful SC does not clear others", "assign writes    = (req_we_i & ~req_amo_i) | (is_sc & sc_ok) | is_amo;",
     "assign writes    = (req_we_i & ~req_amo_i) | is_amo;"),
    ("SC keeps its reservation", "\t\t\t\tif (is_sc)\n\t\t\t\t\tresv_valid_q[req_id_i] <= 1'b0;", ""),
    ("a failed SC goes to memory", "mem_req_valid_o = req_valid_i & ~(is_sc & ~sc_ok);", "mem_req_valid_o = req_valid_i;"),
    ("reservation without the lane", "assign byte_addr = {req_addr_i[X-1:SB], lane};", "assign byte_addr = {req_addr_i[X-1:SB], SB'(0)};"),
    (".W treated as .D", "assign is_w = (X == 32) || (wstrb_q != '1);", "assign is_w = (X == 32);"),
    ("signed MIN compares unsigned", "riscv_pkg::AMO_MIN:  r = ($signed(a_s) < $signed(b_s)) ? a : b;",
     "riscv_pkg::AMO_MIN:  r = (a < b) ? a : b;"),
    ("MAXU compares signed", "riscv_pkg::AMO_MAXU: r = (a > b) ? a : b;",
     "riscv_pkg::AMO_MAXU: r = ($signed(a_s) > $signed(b_s)) ? a : b;"),
    (".W operands not sign-extended", "a_s = X'($signed(a[31:0]));", "a_s = a;"),
    ("result not in its lane", "mem_req_wdata_o = r << (8 * lane_q);", "mem_req_wdata_o = r;"),
    ("AMO responds with the new value", "\t\t\t\trsp_data_o  = old_q;", "\t\t\t\trsp_data_o  = r;"),
    ("failed SC answers in lane 0", "rsp_data_o  = X'(1) << (8 * lane_q);", "rsp_data_o  = X'(1);"),
    ("successful SC answers memory data", "\t\t\t\tif (sc_q) rsp_data_o = '0;", ""),
    ("LR does not reserve", "\t\t\t\t\tresv_valid_q[req_id_i] <= 1'b1;", "\t\t\t\t\tresv_valid_q[req_id_i] <= 1'b0;"),
    ("AMO write offered as a read", "\t\t\tmem_req_we_o    = 1'b1;", "\t\t\tmem_req_we_o    = 1'b0;"),
    ("AND as OR", "riscv_pkg::AMO_AND:  r = a & b;", "riscv_pkg::AMO_AND:  r = a | b;"),
]
