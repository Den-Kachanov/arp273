# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Mutants of rtl/soc/soc_top.sv for tb/unit/test_soc.py (see scripts/mutate.py)"""

RTL = "rtl/soc/soc_top.sv"
TEST = "tb/unit/test_soc.py"
KEXPR = "64"

MUTANTS = [
    ("CLINT not decoded", "assign to_clint = (a_addr[X-1:16] == CLINT_BASE[X-1:16]);", "assign to_clint = 1'b0;"),
    ("CLINT data not returned", "assign d_rsp_d = clint_q ? c_rsp_d : m_rsp_d;", "assign d_rsp_d = m_rsp_d;"),
    ("all harts are hart 0", ".HART_ID  (h),", ".HART_ID  (0),"),
    ("software interrupt lines crossed", ".irq_msip_i         (msip[h]),", ".irq_msip_i         (msip[0]),"),
    ("timer not connected", ".irq_mtip_i         (mtip[h]),", ".irq_mtip_i         (1'b0),"),
    ("the atomics unit sees one hart", ".req_id_i        (d_id),", ".req_id_i        ('0),"),
    ("mtime does not count", ".tick_i      (tick_i),", ".tick_i      (1'b0),"),
]
