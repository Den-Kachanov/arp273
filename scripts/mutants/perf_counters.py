# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/core/perf_counters.sv for tb/unit/test_perf_counters.py (see scripts/mutate.py)"""

RTL = "rtl/core/perf_counters.sv"
TEST = "tb/unit/test_perf_counters.py"

MUTANTS = [
    ("cycles count by 2", "cycles_q  <= cycles_q + 64'd1;", "cycles_q  <= cycles_q + 64'd2;"),
    ("instret counts every cycle", "instret_q <= instret_q + {63'd0, commit_i};", "instret_q <= instret_q + 64'd1;"),
    ("stalls count commits", "stalls_q  <= stalls_q + {63'd0, stall_i};", "stalls_q  <= stalls_q + {63'd0, commit_i};"),
    ("flushes count stalls", "flushes_q <= flushes_q + {63'd0, flush_i};", "flushes_q <= flushes_q + {63'd0, stall_i};"),
    ("cycles not reset", "cycles_q  <= '0;", "cycles_q  <= cycles_q;"),
    ("instret not reset", "instret_q <= '0;", "instret_q <= instret_q;"),
    ("stalls not reset", "stalls_q  <= '0;", "stalls_q  <= stalls_q;"),
    ("flushes not reset", "flushes_q <= '0;", "flushes_q <= flushes_q;"),
]

EQUIVALENT = {}
