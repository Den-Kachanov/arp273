# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/soc/clint.sv for tb/unit/test_clint.py (see scripts/mutate.py)"""

RTL = "rtl/soc/clint.sv"
TEST = "tb/unit/test_clint.py"

MUTANTS = [
    ("mtime ignores tick", "mtime_d = mtime_q + 64'(tick_i);", "mtime_d = mtime_q + 64'(1);"),
    ("mtime does not count", "mtime_d = mtime_q + 64'(tick_i);", "mtime_d = mtime_q;"),
    ("timer compare signed", "mtip_o[h] <= (mtime_q >= mtimecmp_q[h]);",
     "mtip_o[h] <= ($signed(mtime_q) >= $signed(mtimecmp_q[h]));"),
    ("timer compare strict", "mtip_o[h] <= (mtime_q >= mtimecmp_q[h]);", "mtip_o[h] <= (mtime_q > mtimecmp_q[h]);"),
    ("mtimecmp resets to 0", "mtimecmp_q[h] <= '1;", "mtimecmp_q[h] <= '0;"),
    ("msip lines not driven", "msip_o <= msip_q;", "msip_o <= '0;"),
    ("msip at 8-byte steps", "is_msip = (off == 16'(4 * h));", "is_msip = (off == 16'(8 * h));"),
    ("mtimecmp of hart 0 only", "is_mtimecmp = (word == 13'(32'h0800 + 32'(h)));", "is_mtimecmp = (word == 13'h0800);"),
    ("mtime at the wrong offset", "is_mtime = (word == 13'h17FF);", "is_mtime = (word == 13'h17FE);"),
    ("strobes ignored", "if (req_valid_i && req_we_i && req_wstrb_i[l]) begin", "if (req_valid_i && req_we_i) begin"),
    ("write without valid", "if (req_valid_i && req_we_i && req_wstrb_i[l]) begin", "if (req_we_i && req_wstrb_i[l]) begin"),
    ("msip stores the whole byte", "msip_d[h] = req_wdata_i[8 * l];", "msip_d[h] = |req_wdata_i[8 * l +: 8];"),
    ("mtime write lost", "mtime_d[8 * a[2:0] +: 8] = req_wdata_i[8 * l +: 8];", "mtime_d[8 * a[2:0] +: 8] = mtime_d[8 * a[2:0] +: 8];"),
    ("response without request", "rsp_valid_o <= req_valid_i;", "rsp_valid_o <= 1'b1;"),
    ("write returns data", "rsp_data_o <= req_we_i ? '0 : rdata;", "rsp_data_o <= rdata;"),
]
