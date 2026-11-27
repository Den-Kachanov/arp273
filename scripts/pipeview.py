#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Pipeline viewer: run a program on the pipelined core with waves, and write one self-contained HTML page
that shows, cycle by cycle, what every stage holds, why it waits, where operands are forwarded from,
which requests go to memory, which registers and CSRs change, and a pipeline chart (instructions by
cycles). The page is tools/pipeview/pipeview.html with the trace inlined; it needs no server.

Usage: pipeview.py --sim build/core_pipe_trace_sim --elf build/fib.elf --out build/pipeview/fib.html
                   [--cycles 4000] [--take-traps] [--params "MUL_BITS=4 BP=0"] [--mem-stall 0.3]

The simulation is tb/core/main.cpp built with --trace (make pipeview builds it): it samples every
signal once per cycle, just before the clock edge, into a VCD. This script reads that VCD (only the
signals it needs), follows each instruction from ID to WB by the valid and advance signals, and keeps
what the page draws. Cycle 0 is the first cycle after reset.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "tools" / "pipeview" / "pipeview.html"

# signals of core_pipe (and its CSR unit) the page needs; names as in rtl/sol/core_pipe.sv
SIGNALS = [
    "rst_ni",
    # IF
    "pc_f_q", "fetch_issue", "fetch_accept", "imem_req_addr_o", "imem_req_ready_i", "out_q", "out_pc_q",
    "drop_q", "imem_rsp_valid_i", "fq_cnt_q", "fq_push", "redirect", "ex_redirect", "wb_redirect",
    "redirect_pc", "wb_target", "id_pc",
    # ID
    "id_valid", "id_advance", "id_inst",
    # EX
    "ex_valid_q", "ex_pc_q", "ex_inst_q", "ex_advance", "ex_ops_ready", "ex_rs1_q", "ex_rs2_q", "ex_use_rs1_q",
    "ex_use_rs2_q", "ex_rs1", "ex_rs2", "ex_result", "ex_muldiv_q", "md_busy_q", "md_have_q", "md_done",
    "mem_fwd_ok", "wb_fwd_ok", "mem_can_accept", "ex_trap", "ex_mispredict",
    # MEM
    "mem_valid_q", "mem_pc_q", "mem_inst_q", "mem_advance", "mem_rd_q", "mem_trap", "mem_result_q",
    "dmem_req_valid_o", "dmem_req_ready_i", "dmem_req_addr_o", "dmem_req_we_o", "dmem_req_wstrb_o",
    "dmem_req_wdata_o", "dmem_req_amo_o", "dmem_req_atop_o", "wb_can_accept",
    # WB
    "wb_valid_q", "wb_pc_q", "wb_inst_q", "wb_advance", "wb_wait", "wb_exc", "wb_irq", "wb_we", "wb_rd",
    "wb_data", "wb_rd_q", "wb_mem_re_q", "wb_mem_we_q", "wb_wfi_q", "dmem_rsp_valid_i", "dmem_rsp_data_i",
    "wb_cause",
    # interrupts, counters
    "irq_msip_i", "irq_mtip_i", "irq_meip_i", "perf_cycles_o", "perf_instret_o",
    # CSR unit
    "u_csr.mie_q", "u_csr.mpie_q", "u_csr.mie_bits_q", "u_csr.irq_q", "u_csr.mtvec_q", "u_csr.mepc_q",
    "u_csr.mcause_q", "u_csr.mtval_q", "u_csr.mscratch_q",
]
AMO_NAMES = {0: "add", 1: "swap", 2: "lr", 3: "sc", 4: "xor", 8: "or", 12: "and", 16: "min", 20: "max",
             24: "minu", 28: "maxu"}


def read_vcd(path: Path, scope_top: str, wanted: list[str]) -> list[dict[str, int | None]]:
    """
    One dict per VCD time (one per cycle): signal -> value (None for x or z). Only `wanted` signals,
    named relative to the scope `scope_top` (dots for sub-scopes)
    """
    ids: dict[str, list[str]] = {}
    scope: list[str] = []
    want = set(wanted)
    samples = []
    cur: dict[str, int | None] = {}
    have_time = False
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("$scope"):
                scope.append(line.split()[2])
            elif line.startswith("$upscope"):
                scope.pop()
            elif line.startswith("$var"):
                parts = line.split()
                code, name = parts[3], parts[4]
                path_ = scope[1:]   # drop TOP
                if path_ and path_[0] == scope_top:
                    full = ".".join(path_[1:] + [name])
                    if full in want:
                        ids.setdefault(code, []).append(full)
            elif line.startswith("#"):
                if have_time:
                    samples.append(dict(cur))
                have_time = True
            elif line[0] in "bB":
                val, code = line[1:].split()
                if code in ids:
                    v = None if any(ch in val for ch in "xXzZ") else int(val, 2)
                    for n in ids[code]:
                        cur[n] = v
            elif line[0] in "01xXzZ" and len(line) > 1:
                code = line[1:]
                if code in ids:
                    v = None if line[0] in "xXzZ" else int(line[0])
                    for n in ids[code]:
                        cur[n] = v
    if have_time:
        samples.append(dict(cur))
    missing = want - {n for ns in ids.values() for n in ns}
    if missing:
        sys.exit(f"pipeview: signals not in the VCD: {', '.join(sorted(missing))}")
    return samples


def disassemble(elf: Path, cross: str) -> tuple[list[dict], dict[int, str]]:
    """objdump -d: the listing (pc, hex, text, label) and pc -> text"""
    out = subprocess.run([f"{cross}objdump", "-d", "-M", "numeric", str(elf)],
                         check=True, capture_output=True, text=True).stdout
    listing, by_pc, label = [], {}, None
    for line in out.splitlines():
        m = re.match(r"^([0-9a-f]+) <(.+)>:$", line)
        if m:
            label = m.group(2)
            continue
        m = re.match(r"^\s+([0-9a-f]+):\s+([0-9a-f]{8})\s+(.*)$", line)
        if m:
            pc, text = int(m.group(1), 16), " ".join(m.group(3).split("#")[0].split())
            listing.append({"pc": f"{pc:x}", "hex": m.group(2), "text": text, "label": label})
            by_pc[pc] = text
            label = None
    return listing, by_pc


def hx(v):
    return None if v is None else f"{v:x}"


def fwd_source(s, rs, use):
    """where EX takes an operand from in cycle sample s: MEM, WB, RF (register file), or None (not used)"""
    def g(n):
        return s.get(n) or 0
    if not g("ex_valid_q") or not g(use) or g(rs) == 0:
        return None
    if g("mem_fwd_ok") and g("mem_rd_q") == g(rs):
        return "MEM"
    if g("wb_fwd_ok") and g("wb_rd_q") == g(rs):
        return "WB"
    return "RF"


def build_trace(samples, by_pc):
    """the cycles the page draws, the instruction instances and their pipeline chart"""
    # drop the reset cycles: cycle 0 is the first with rst_ni = 1
    start = next(i for i, s in enumerate(samples) if s.get("rst_ni") == 1)
    samples = samples[start:]
    cycles = []
    inst = []                    # instruction instances in program order of entering ID
    cur = {"id": None, "ex": None, "mem": None, "wb": None}
    prev = None
    regs_writes = []

    def g(s, n, d=0):
        v = s.get(n)
        return d if v is None else v

    for c, s in enumerate(samples):
        # instances in each stage (module docstring)
        nxt = {}
        if g(s, "id_valid"):
            if prev is None or g(prev, "id_advance") or not g(prev, "id_valid") or g(prev, "redirect"):
                inst.append({"pc": g(s, "id_pc"), "cells": {}, "end": None})
                nxt["id"] = len(inst) - 1
            else:
                nxt["id"] = cur["id"]
        for stage, valid, src, adv in (("ex", "ex_valid_q", "id", "id_advance"),
                                        ("mem", "mem_valid_q", "ex", "ex_advance"),
                                        ("wb", "wb_valid_q", "mem", "mem_advance")):
            if g(s, valid):
                nxt[stage] = cur[src] if (prev is not None and g(prev, adv)) else cur[stage]
        cur = {k: nxt.get(k) for k in ("id", "ex", "mem", "wb")}

        # why a stage does not hand its instruction on
        why = {}
        if g(s, "wb_valid_q") and not g(s, "wb_advance"):
            if g(s, "wb_exc"):
                why["wb"] = "interrupt taken" if g(s, "wb_irq") else "exception taken"
            elif g(s, "wb_wfi_q"):
                why["wb"] = "WFI: waiting for an interrupt"
            else:
                why["wb"] = "waiting for the data response"
        if g(s, "mem_valid_q") and not g(s, "mem_advance"):
            why["mem"] = "WB is busy" if not g(s, "wb_can_accept") else "data port not ready"
        if g(s, "ex_valid_q") and not g(s, "ex_advance") and not g(s, "wb_redirect"):
            if not g(s, "ex_ops_ready"):
                why["ex"] = "load-use: operand arrives from memory"
            elif g(s, "ex_muldiv_q") and not (g(s, "md_have_q") or g(s, "md_done")):
                why["ex"] = "multiplier/divider working"
            else:
                why["ex"] = "MEM is busy"
        if g(s, "id_valid") and not g(s, "id_advance") and not g(s, "redirect"):
            why["id"] = "EX is busy"

        # chart cells
        for stage in ("id", "ex", "mem", "wb"):
            k = cur[stage]
            if k is None:
                continue
            state = stage.upper()
            if stage in why:
                state += "*"
            if stage == "wb" and g(s, "wb_advance"):
                inst[k]["end"] = ("commit", c)
            if stage == "wb" and g(s, "wb_exc"):
                inst[k]["end"] = ("irq" if g(s, "wb_irq") else "trap", c)
            inst[k]["cells"][c] = state

        if g(s, "wb_we") and g(s, "wb_rd"):
            regs_writes.append((c, g(s, "wb_rd"), hx(g(s, "wb_data"))))

        mem_req = None
        if g(s, "dmem_req_valid_o"):
            amo = g(s, "dmem_req_amo_o")
            kind = ("amo" + "." + AMO_NAMES.get(g(s, "dmem_req_atop_o"), "?")) if amo else (
                "store" if g(s, "dmem_req_we_o") else "load")
            mem_req = {"kind": kind, "addr": hx(g(s, "dmem_req_addr_o")), "data": hx(g(s, "dmem_req_wdata_o")),
                       "strb": g(s, "dmem_req_wstrb_o"), "ok": g(s, "dmem_req_ready_i")}
        mispredicted = g(s, "ex_redirect")
        cycles.append({
            "n": c,
            "i": [cur["id"], cur["ex"], cur["mem"], cur["wb"]],
            "why": why,
            "f": {"pc": hx(g(s, "pc_f_q")), "req": hx(g(s, "imem_req_addr_o")) if g(s, "fetch_issue") else None,
                  "acc": g(s, "fetch_accept"), "out": hx(g(s, "out_pc_q")) if g(s, "out_q") else None,
                  "drop": g(s, "drop_q"), "rsp": g(s, "imem_rsp_valid_i"), "q": g(s, "fq_cnt_q")},
            "x": {"a": hx(g(s, "ex_rs1")), "b": hx(g(s, "ex_rs2")), "r": hx(g(s, "ex_result")),
                  "fa": fwd_source(s, "ex_rs1_q", "ex_use_rs1_q"), "fb": fwd_source(s, "ex_rs2_q", "ex_use_rs2_q"),
                  "ra": g(s, "ex_rs1_q"), "rb": g(s, "ex_rs2_q"),
                  "md": bool(g(s, "ex_valid_q") and g(s, "ex_muldiv_q"))},
            "br": {"ex": hx(g(s, "redirect_pc")) if mispredicted else None,
                   "wb": hx(g(s, "wb_target")) if g(s, "wb_redirect") else None},
            "m": mem_req,
            "rsp": hx(g(s, "dmem_rsp_data_i")) if g(s, "dmem_rsp_valid_i") else None,
            "w": [g(s, "wb_rd"), hx(g(s, "wb_data"))] if (g(s, "wb_we") and g(s, "wb_rd")) else None,
            "exc": ({"irq": g(s, "wb_irq"), "cause": g(s, "wb_cause")} if g(s, "wb_exc") else None),
            "irq": [g(s, "irq_msip_i"), g(s, "irq_mtip_i"), g(s, "irq_meip_i")],
            "csr": {"mstatus.MIE": g(s, "u_csr.mie_q"), "mstatus.MPIE": g(s, "u_csr.mpie_q"),
                    "mie": g(s, "u_csr.mie_bits_q"), "mip": g(s, "u_csr.irq_q"), "mtvec": hx(g(s, "u_csr.mtvec_q")),
                    "mepc": hx(g(s, "u_csr.mepc_q")), "mcause": hx(g(s, "u_csr.mcause_q")),
                    "mtval": hx(g(s, "u_csr.mtval_q")), "mscratch": hx(g(s, "u_csr.mscratch_q"))},
            "cnt": [g(s, "perf_cycles_o"), g(s, "perf_instret_o")],
        })
        prev = s

    for k in inst:
        k["text"] = by_pc.get(k["pc"], "?")
        k["pc"] = hx(k["pc"])
        k["cells"] = {str(c): v for c, v in k["cells"].items()}
        if k["end"] is None:
            k["end"] = ("flush", max(int(c) for c in k["cells"])) if k["cells"] else ("flush", 0)
    return cycles, inst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", required=True, type=Path)
    ap.add_argument("--elf", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cycles", type=int, default=4000, help="stop after this many cycles")
    ap.add_argument("--take-traps", action="store_true",
                    help="the core enters its handler on exceptions and interrupts (TRAP_STOP=0)")
    ap.add_argument("--params", default="", help="parameters of the build, shown on the page")
    ap.add_argument("--mem-stall", type=float, default=0.0, help="MEM_STALL of the memory model")
    ap.add_argument("--cross", default="riscv64-elf-")
    args = ap.parse_args()

    binf = args.elf.with_suffix(".bin")
    nm = subprocess.run([f"{args.cross}nm", str(args.elf)], check=True, capture_output=True, text=True).stdout
    tohost = next(p[0] for p in (ln.split() for ln in nm.splitlines()) if len(p) == 3 and p[2] == "tohost")
    with tempfile.TemporaryDirectory(prefix="pipeview_") as tmp:
        vcd = Path(tmp) / "waves.vcd"
        env = dict(os.environ, VCD_FILE=str(vcd), MEM_STALL=str(args.mem_stall))
        if args.take_traps or "/irq/" in str(args.elf) or args.elf.stem in irq_programs():
            env["TRAP_STOP"] = "0"
        run = subprocess.run([str(args.sim), str(binf), os.devnull, tohost, str(args.cycles)],
                             capture_output=True, text=True, env=env)
        samples = read_vcd(vcd, "core_pipe", SIGNALS)
    listing, by_pc = disassemble(args.elf, args.cross)
    cycles, inst = build_trace(samples, by_pc)
    trace = {
        "program": args.elf.stem, "params": args.params or "defaults (Sol 2)",
        "memory": "BRAM (1-cycle)" if not args.mem_stall else f"stalling memory, MEM_STALL={args.mem_stall}",
        "result": run.stderr.strip().splitlines()[:3],
        "listing": listing, "cycles": cycles, "inst": inst,
    }
    page = TEMPLATE.read_text().replace("__TRACE__", json.dumps(trace, separators=(",", ":")).replace("</", "<\\/"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"pipeview: {len(cycles)} cycles, {len(inst)} instructions -> {args.out}")


def irq_programs():
    return {p.stem for p in (REPO / "sw" / "irq").glob("*.c")}


if __name__ == "__main__":
    main()
