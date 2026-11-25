# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/core/muldiv.sv for tb/unit/test_muldiv.py (see scripts/mutate.py)"""

RTL = "rtl/core/muldiv.sv"
TEST = "tb/unit/test_muldiv.py"

MUTANTS = [
    ("no subtract in the last step", "assign sub  = is_div_q | (last_q & b_signed_q);", "assign sub  = is_div_q;"),
    ("logical shift for signed a", "assign mul_hi = {a_signed_q & sum[X], sum[X:1]};", "assign mul_hi = {1'b0, sum[X:1]};"),
    ("quotient negated on division by zero", "((sign_a ^ sign_b) & ~b_is_zero)", "(sign_a ^ sign_b)"),
    ("remainder from hi[X-1:0]", "is_div_q ? hi_q[X:1] : hi_q[X-1:0]", "hi_q[X-1:0]"),
    ("MULHSU with signed b", "3'b010:  a_signed = 1'b1;", "3'b010:  begin a_signed = 1'b1; b_signed = 1'b1; end"),
    ("remainder takes the quotient sign", "funct3_i[1] ? sign_a :", "funct3_i[1] ? (sign_a ^ sign_b) :"),
    ("W result not sign-extended",
     "assign result_o = word_q ? {{(X-32){signed_res[31]}}, signed_res[31:0]} : signed_res;",
     "assign result_o = signed_res;"),
    ("one iteration too few", "cnt_q      <= short_op ? CW'(H - 1) : CW'(X - 1);", "cnt_q      <= short_op ? CW'(H - 1) : CW'(X - 2);"),
    ("DONE does not return to idle", "S_DONE:               state_q <= S_IDLE;", "S_DONE:               state_q <= S_DONE;"),
    ("MULHU treats a as signed", "3'b010:  a_signed = 1'b1;", "3'b010, 3'b011:  a_signed = 1'b1;"),
    ("W operands: a sign-extended when unsigned", "{{(X-32){a_signed & a_i[31]}}", "{{(X-32){a_i[31]}}"),
    ("W operands: b sign-extended when unsigned", "{{(X-32){b_signed & b_i[31]}}", "{{(X-32){b_i[31]}}"),
    ("pick_hi for MULH wrong", "(funct3_i[1] | funct3_i[0])", "funct3_i[1]"),
    ("borrow from sum[X-1]", "assign ge     = ~sum[X];", "assign ge     = ~sum[X-1];"),
    ("borrow inverted", "assign ge     = ~sum[X];", "assign ge     = sum[X];"),
    ("multiplicand not sign-extended", "{a_signed & a_eff[X-1], a_eff}", "{1'b0, a_eff}"),
    ("new remainder keeps old value when ge", "assign rem_n  = ge ? sum[X-1:0] : hi_q[X-1:0];", "assign rem_n  = hi_q[X-1:0];"),
    ("quotient bit inverted", "assign div_lo = {lo_q[X-2:0], ge};", "assign div_lo = {lo_q[X-2:0], ~ge};"),
    ("W flag not remembered", "word_q     <= word_i;", "word_q     <= 1'b0;"),
    ("operation not remembered (pick_hi)", "pick_hi_q  <= pick_hi;", "pick_hi_q  <= 1'b0;"),
    # last_q (ADR 011)
    ("last step one cycle early", "last_q <= (cnt_q == CW'(1));", "last_q <= (cnt_q == CW'(2));"),
    ("last step flag kept from the previous operation", "last_q     <= 1'b0;", "last_q     <= last_q;"),
    # short path (ADR 011)
    ("short path for MULH", "assign short_mul = ~is_div & ~pick_hi & ~|b_eff[X-1:H];", "assign short_mul = ~is_div & ~|b_eff[X-1:H];"),
    ("short MUL ignores bit H of b", "~|b_eff[X-1:H];", "~|b_eff[X-1:H+1];"),
    ("short MUL never taken", "assign short_mul = ~is_div & ~pick_hi & ~|b_eff[X-1:H];", "assign short_mul = 1'b0;"),
    ("short division by zero", "assign short_div = is_div & a_fits & ~b_is_zero;", "assign short_div = is_div & a_fits;"),
    ("negative -2^H fits", "(&a_eff[X-1:H] & |a_eff[H-1:0])", "(&a_eff[X-1:H])"),
    ("negative dividend never fits", "(&a_eff[X-1:H] & |a_eff[H-1:0])", "1'b0"),
    ("signed dividend read as unsigned", "assign a_fits    = sign_a ?", "assign a_fits    = 1'b0 ?"),
    ("dividend not moved up", "assign dividend = short_div ? {mag_a[H-1:0], {H{1'b0}}} : mag_a;", "assign dividend = mag_a;"),
    ("short path one iteration more", "cnt_q      <= short_op ? CW'(H - 1) : CW'(X - 1);", "cnt_q      <= short_op ? CW'(H) : CW'(X - 1);"),
    ("short MUL picks lo", "else if (short_q && !is_div_q) picked = {hi_q[H-1:0], lo_q[X-1:H]};", "else if (1'b0) picked = lo_q;"),
    ("short MUL picks the wrong half", "picked = {hi_q[H-1:0], lo_q[X-1:H]};", "picked = {lo_q[X-1:H], hi_q[H-1:0]};"),
    ("short flag not stored", "short_q    <= short_op;", "short_q    <= 1'b0;"),
]

EQUIVALENT = {}
