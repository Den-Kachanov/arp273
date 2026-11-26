# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/core/bpred.sv for tb/unit/test_bpred.py (see scripts/mutate.py)"""

RTL = "rtl/core/bpred.sv"
TEST = "tb/unit/test_bpred.py"
KEXPR = "64"

MUTANTS = [
    ("BTB index from the wrong bits", "assign idx     = pc_i[2 +: IB];", "assign idx     = pc_i[3 +: IB];"),
    ("tag not compared", "assign hit     = btb_valid_q[idx] & (btb_tag_q[idx] == pc_i[2+IB +: TAG_BITS]);",
     "assign hit     = btb_valid_q[idx];"),
    ("counter ignored", "assign taken_o = hit & ((kind != K_BR) | bht_q[hidx][1]);", "assign taken_o = hit;"),
    ("counter overflows past 3", "if (upd_taken_i && bht_q[u_hidx] != 2'd3)", "if (upd_taken_i)"),
    ("counter underflows past 0", "else if (!upd_taken_i && bht_q[u_hidx] != 2'd0)", "else if (!upd_taken_i)"),
    ("new branch starts strongly taken", "bht_q[u_hidx] <= 2'd2;", "bht_q[u_hidx] <= 2'd3;"),
    ("valid bits not reset", "btb_valid_q[i] <= 1'b0;", "btb_valid_q[i] <= btb_valid_q[i];"),
    ("not-taken outcome written", "assign u_write = upd_i & upd_taken_i & u_fits;", "assign u_write = upd_i & u_fits;"),
    ("far target written", "assign u_fits  = (upd_kind_i == K_RET) |", "assign u_fits  = 1'b1 |"),
    ("return target from the BTB", "assign tgt_lo  = (kind == K_RET) ? ras_q[top] : btb_tgt_q[idx];",
     "assign tgt_lo  = btb_tgt_q[idx];"),
    ("stack top at the free slot", "assign tgt_lo  = (kind == K_RET) ? ras_q[top]", "assign tgt_lo  = (kind == K_RET) ? ras_q[ras_ptr_q]"),
    ("push of pc instead of pc + 4", "ras_q[ras_ptr_q] <= push_pc[TARGET_BITS-1:2];", "ras_q[ras_ptr_q] <= pc_i[TARGET_BITS-1:2];"),
    ("push without accept", "if (accept_i && hit && kind == K_CALL)     ras_ptr_q", "if (hit && kind == K_CALL)     ras_ptr_q"),
    ("pop without accept", "else if (accept_i && hit && kind == K_RET) ras_ptr_q", "else if (hit && kind == K_RET) ras_ptr_q"),
    ("push and pop swapped", "if (accept_i && hit && kind == K_CALL)     ras_ptr_q <= ras_ptr_q + RB'(1);",
     "if (accept_i && hit && kind == K_CALL)     ras_ptr_q <= top;"),
    ("kind not stored", "btb_kind_q[u_idx] <= upd_kind_i;", "btb_kind_q[u_idx] <= 2'd0;"),
    ("target upper bits from zero", "assign target_o = {pc_i[X-1:TARGET_BITS], tgt_lo, 2'b00};",
     "assign target_o = {{(X-TARGET_BITS){1'b0}}, tgt_lo, 2'b00};"),
]

EQUIVALENT = {}
