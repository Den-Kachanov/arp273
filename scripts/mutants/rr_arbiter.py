# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/soc/rr_arbiter.sv for tb/unit/test_rr_arbiter.py (see scripts/mutate.py)"""

RTL = "rtl/soc/rr_arbiter.sv"
TEST = "tb/unit/test_rr_arbiter.py"

MUTANTS = [
    ("fixed priority", "prio_q  <= IW'((32'(pick) + 1) % N);", "prio_q  <= '0;"),
    ("grant while busy", "assign free = ~busy_q | rsp_valid_i;", "assign free = 1'b1;"),
    ("no grant in the response cycle", "assign free = ~busy_q | rsp_valid_i;", "assign free = ~busy_q;"),
    ("response always to master 0", "rsp_valid_o[owner_q] = busy_q & rsp_valid_i;", "rsp_valid_o[0] = busy_q & rsp_valid_i;"),
    ("owner not saved", "owner_q <= pick;", "owner_q <= owner_q;"),
    ("ready without the port's ready", "req_ready_o[pick] = free & found & req_ready_i;", "req_ready_o[pick] = free & found;"),
    ("valid without a request", "assign req_valid_o = free & found;", "assign req_valid_o = free;"),
    ("the last requester wins", "if (!found && req_valid_i[m]) begin", "if (req_valid_i[m]) begin"),
    ("busy never cleared", "end else if (rsp_valid_i) begin\n\t\t\t\tbusy_q <= 1'b0;", "end else if (1'b0) begin\n\t\t\t\tbusy_q <= 1'b0;"),
    ("response without a request in flight", "rsp_valid_o[owner_q] = busy_q & rsp_valid_i;", "rsp_valid_o[owner_q] = rsp_valid_i;"),
]

EQUIVALENT = {
    "response without a request in flight": "the port below answers only requests it has accepted (the protocol), so "
                                            "rsp_valid_i is never 1 while nothing is in flight; busy_q there is a guard",
}
