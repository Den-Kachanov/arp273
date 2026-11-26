# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/common/csr.sv, checked by the CSR and exception tests of the pipelined core (see scripts/mutate.py)"""

RTL = "rtl/common/csr.sv"
TEST = ["tb/unit/test_csr.py", "tb/unit/test_atomic.py"]
KEXPR = "64"

MUTANTS = [
    ("misa without A", "26'h0001101", "26'h0001100"),
    ("mhartid reads 0", "12'hF14: old = X'(HART_ID);", "12'hF14: old = '0;"),
    ("set acts as write", "2'b10:   new_val = old | wdata_i;", "2'b10:   new_val = wdata_i;"),
    ("clear keeps the bits", "2'b11:   new_val = old & ~wdata_i;", "2'b11:   new_val = old & wdata_i;"),
    ("read-only CSRs writable", "assign writable  = (addr_i[11:10] != 2'b11);", "assign writable  = 1'b1;"),
    ("missing CSRs exist", "assign illegal_o = ~exists | (write_i & ~writable);", "assign illegal_o = write_i & ~writable;"),
    ("write without write_i", "assign do_write = access_i & write_i & ~illegal_o & ~trap_i;",
     "assign do_write = access_i & ~illegal_o & ~trap_i;"),
    ("MPIE not saved", "			mpie_q   <= mie_q;\n", ""),
    ("MIE not cleared on trap", "			mie_q    <= 1'b0;\n", ""),
    ("MRET does not set MPIE", "			mpie_q <= 1'b1;\n", ""),
    ("MRET does not restore MIE", "			mie_q  <= mpie_q;\n", ""),
    ("MPP reads 0", "		mstatus[12] = 1'b1;\n		mstatus[11] = 1'b1;\n", ""),
    ("mtvec keeps bits 1:0", "12'h305: mtvec_q    <= {new_val[X-1:2], 2'b00};", "12'h305: mtvec_q    <= new_val;"),
    ("mepc keeps bits 1:0", "12'h341: mepc_q     <= {new_val[X-1:2], 2'b00};", "12'h341: mepc_q     <= new_val;"),
    ("mtval not written on trap", "			mtval_q  <= tval_i;\n", ""),
    ("misa without M", "26'h0001101", "26'h0000101"),
    ("mie bits shuffled", "12'h304: mie_bits_q <= {new_val[11], new_val[7], new_val[3]};",
     "12'h304: mie_bits_q <= {new_val[7], new_val[11], new_val[3]};"),
    ("instret reads cycles", "12'hB02, 12'hC02: old = instret_i[X-1:0];", "12'hB02, 12'hC02: old = cycle_i[X-1:0];"),
    ("misa writable", "12'h301: old = MISA;", "12'h301: old = mscratch_q;"),
]

EQUIVALENT = {
    "write without write_i": "write_i is 0 only for CSRRS/CSRRC with rs1 = x0 or uimm = 0: the written value is then "
                             "old | 0 or old & ~0, the old value. It matters for the legality check (read-only CSRs) and "
                             "for future CSRs with side effects on write",
}
