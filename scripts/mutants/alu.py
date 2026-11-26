# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/common/alu.sv for tb/unit/test_alu.py (see scripts/mutate.py)"""

RTL = "rtl/common/alu.sv"
TEST = "tb/unit/test_alu.py"

MUTANTS = [
    # operation select
    ("SLTU does not subtract", "assign is_sub = (is_add & alt) | is_cmp;", "assign is_sub = (is_add & alt) | is_slt;"),
    ("ADD subtracts", "assign is_sub = (is_add & alt) | is_cmp;", "assign is_sub = is_add | is_cmp;"),
    ("SRA is SRL", "assign is_sra = is_shr & alt;", "assign is_sra = 1'b0;"),
    ("SLL selected as SRL", "assign is_sll = f3 == 3'b001;", "assign is_sll = 1'b0;"),
    # adder and comparison
    ("SLT is unsigned", "assign a_ext = is_slt & a_i[X-1];", "assign a_ext = 1'b0;"),
    ("SLT: b not sign-extended", "assign b_ext = is_slt & b_i[X-1];", "assign b_ext = 1'b0;"),
    ("SLTU sign-extends a", "assign a_ext = is_slt & a_i[X-1];", "assign a_ext = a_i[X-1];"),
    ("no carry-in on subtract", "+ (X+1)'(is_sub);", ";"),
    ("comparison from bit X-1", "(is_cmp & sum[X]);", "(is_cmp & sum[X-1]);"),
    # shifter
    ("W shift amount has 6 bits", "shamt = word_i ? {1'b0, b_i[4:0]} : b_i[SHW-1:0];", "shamt = b_i[SHW-1:0];"),
    ("SRAW takes the sign from bit 63", "sh_in[i] = is_sra & a_i[31];", "sh_in[i] = is_sra & a_i[63];"),
    ("SRLW keeps the upper word", "else if (word_i && i >= 32) sh_in[i] = is_sra & a_i[31];", "else if (1'b0) sh_in[i] = 1'b0;"),
    ("SRA fills with zeros", "assign fill = is_sra & a_i[X-1];", "assign fill = 1'b0;"),
    ("SRL fills with the sign", "assign fill = is_sra & a_i[X-1];", "assign fill = a_i[X-1];"),
    ("SLL does not reverse the input", "if (is_sll)                 sh_in[i] = a_i[X-1-i];", "if (1'b0)                   sh_in[i] = a_i[X-1-i];"),
    ("SLL output not reversed", "(is_sll & sh_r[X-1-i])", "(is_sll & sh_r[i])"),
    # result
    ("W result not sign-extended", "assign r_o = word_i ? {{32{r_full[31]}}, r_full[31:0]} : r_full;", "assign r_o = r_full;"),
    ("XOR is OR", "(is_xor & (a_i[i] ^ b_i[i]))", "(is_xor & (a_i[i] | b_i[i]))"),
    ("AND is OR", "(is_and & (a_i[i] & b_i[i]))", "(is_and & (a_i[i] | b_i[i]))"),
    ("OR is XOR", "(is_or  & (a_i[i] | b_i[i]))", "(is_or  & (a_i[i] ^ b_i[i]))"),
    ("SRA result lost", "(is_shr & sh_r[i]);", "((is_shr & ~alt) & sh_r[i]);"),
    ("comparison result dropped", "r_full[0] = r_full[0] | (is_cmp & sum[X]);", "r_full[0] = r_full[0];"),
]

EQUIVALENT = {}
