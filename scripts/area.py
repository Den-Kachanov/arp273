#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Area estimate of every module of the cores (rtl/common, rtl/sol, rtl/ref) with Yosys (the numbers quoted in docs/)

Usage: area.py [--xlen 64 32] [--xc7] [--commit HASH] [--json FILE] [--svg FILE]

For each module, alone, and for the whole core (flattened):
  LUT6   `synth -top <module>; abc -lut 6`
  FF     flip-flops in that netlist
  cells  generic gates after `synth` only (before LUT mapping)
The core is also synthesized with a stub in place of `muldiv`, to see what the M extension adds

--xc7 adds a second table from `synth_xilinx -family xc7 -abc9` (Xilinx 7 series, the Arty A7
candidate): LUT, LUTs used as distributed RAM (the regfile maps there), CARRY4 (the dedicated
carry chain) and the latest arrival time from `sta` with
the cell delays of the Yosys Xilinx library. That time is logic only, without routing, which on
an FPGA is often as large again; use it to compare variants, never as Fmax

This is an estimate of area, not of Fmax or power: there is no timing and no place-and-route.
ABC is heuristic, so numbers move by a few percent between tool versions; compare only
numbers from one run. A figure quoted in a document needs the date and the commit hash:
pass the hash with --commit (for example --commit "$(git rev-parse --short HEAD)"), this
script does not run git itself
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import rtl_files

REPO = Path(__file__).resolve().parents[1]
# top-level cores: synthesized flattened, listed after the modules
TOPS = ("core", "core_pipe")
# --release: the pipelined core without its verification-only parts (parameter DEBUG = 0)
RELEASE = {"on": False}
# --param NAME=VALUE: parameters of the pipelined core (for example MUL_BITS=4, ADR 017)
CORE_PARAMS: dict[str, str] = {}


def read_cmd(files, xlen, top):
    """read_verilog, and for a release build the DEBUG parameter of core_pipe set to 0"""
    cmd = f"read_verilog -sv -DXLEN={xlen} {' '.join(str(f) for f in files)}; "
    if top == "core_pipe" and (RELEASE["on"] or CORE_PARAMS):
        params = ({"DEBUG": "0"} if RELEASE["on"] else {}) | CORE_PARAMS
        cmd += f"hierarchy -top core_pipe {' '.join(f'-chparam {k} {v}' for k, v in params.items())}; "
    elif top == "muldiv" and "MUL_BITS" in CORE_PARAMS:
        cmd += f"hierarchy -top muldiv -chparam MUL_BITS {CORE_PARAMS['MUL_BITS']}; "
    return cmd
STUB = """module muldiv #(parameter int MUL_BITS = 1) (
\tinput  logic clk_i, rst_ni, valid_i, abort_i,
\tinput  logic [2:0] funct3_i,
\tinput  logic word_i,
\tinput  logic [riscv_pkg::XLEN-1:0] a_i, b_i,
\toutput logic done_o,
\toutput logic [riscv_pkg::XLEN-1:0] result_o
);
\tassign done_o = 1'b1;
\tassign result_o = '0;
\tlogic unused;
\tassign unused = &{1'b0, clk_i, rst_ni, valid_i, abort_i, funct3_i, word_i, a_i, b_i};
endmodule
"""


def modules():
    """Names of the modules of the cores (the cores themselves last)"""
    names = []
    for f in rtl_files.files(REPO):
        names += re.findall(r"^\s*module\s+(\w+)", f.read_text(), re.M)
    return [n for n in names if n not in TOPS] + [t for t in TOPS if t in names]


def synth(files, xlen, top, flatten, lut, workdir):
    """Run Yosys once in its own directory, return the text of `stat`"""
    # every run gets a private directory: runs are parallel, and two of them can share a top name
    # (the core with and without the muldiv stub); abc also writes abc.history into the current directory
    run_dir = Path(tempfile.mkdtemp(dir=workdir))
    out = run_dir / "stat.txt"
    script = (read_cmd(files, xlen, top) +
              f"synth {'-flatten' if flatten else ''} -top {top}; "
              f"{'abc -lut 6; ' if lut else ''}tee -o {out} stat")
    subprocess.run(["yosys", "-q", "-p", script], check=True, capture_output=True, cwd=run_dir)
    return out.read_text()


def count(text, pattern):
    return sum(int(m.group(1)) for m in re.finditer(pattern, text, re.M))


def measure(files, xlen, top, flatten, workdir):
    lut_text = synth(files, xlen, top, flatten, True, workdir)
    gen_text = synth(files, xlen, top, flatten, False, workdir)
    return {
        "lut": count(lut_text, r"^\s+(\d+)\s+\$lut"),
        "ff": count(lut_text, r"^\s+(\d+)\s+\S*DFF"),
        "cells": count(gen_text, r"^\s+(\d+)\s+cells\s*$"),
    }


def measure_xc7(files, xlen, top, flatten, workdir):
    """Xilinx 7 series: LUT, CARRY4, MUXF and the logic-only arrival time in ps"""
    run_dir = Path(tempfile.mkdtemp(dir=workdir))
    script = (read_cmd(files, xlen, top) +
              f"synth_xilinx -family xc7 -abc9 {'-flatten' if flatten else ''} -top {top}; "
              f"tee -o {run_dir / 'stat.txt'} stat; "
              f"read_verilog -lib -specify +/xilinx/cells_sim.v; tee -o {run_dir / 'sta.txt'} sta")
    subprocess.run(["yosys", "-q", "-p", script], check=True, capture_output=True, cwd=run_dir)
    stat = (run_dir / "stat.txt").read_text()
    arrival = re.search(r"Latest arrival time in '\S+' is (\d+)", (run_dir / "sta.txt").read_text())
    return {
        "lut": count(stat, r"^\s+(\d+)\s+LUT[1-6]\s*$"),
        # LUTs of a SLICEM used as RAM: RAM32M and RAM64M take 4 each, the single-bit dual-port ones 2
        "lutram": 4 * count(stat, r"^\s+(\d+)\s+RAM(?:32|64)M\s*$")
                  + 2 * count(stat, r"^\s+(\d+)\s+RAM(?:32|64)X1D\s*$")
                  + count(stat, r"^\s+(\d+)\s+RAM(?:32|64)X1S\s*$"),
        "carry4": count(stat, r"^\s+(\d+)\s+CARRY4\s*$"),
        "muxf": count(stat, r"^\s+(\d+)\s+MUXF[78]\s*$"),
        "ps": int(arrival.group(1)) if arrival else None,
    }


def run_xc7(xlens, workdir):
    pkg = sorted((REPO / "rtl" / "pkg").glob("*.sv"))
    core_files = rtl_files.files(REPO)
    with ThreadPoolExecutor(8) as pool:
        jobs = {(m, x): pool.submit(measure_xc7, pkg + core_files, x, m, m in TOPS, workdir)
                for x in xlens for m in modules()}
        return {f"{m}_{x}": f.result() for (m, x), f in jobs.items()}


def table_xc7(results, xlens):
    names = modules()
    head = "| Module | " + " | ".join(f"LUT / LUTRAM / CARRY4 / MUXF / ps, XLEN = {x}" for x in xlens) + " |"
    lines = [head, "|---|" + "---|" * len(xlens)]
    for m in names:
        cells = [f"{r['lut']} / {r['lutram']} / {r['carry4']} / {r['muxf']} / {r['ps']}"
                 for r in (results[f"{m}_{x}"] for x in xlens)]
        lines.append(f"| `{m}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run_all(xlens, workdir):
    pkg = sorted((REPO / "rtl" / "pkg").glob("*.sv"))
    core_files = rtl_files.files(REPO)
    stub = workdir / "muldiv_stub.sv"
    stub.write_text(STUB)
    nostub_files = pkg + [f for f in core_files if f.stem != "muldiv"] + [stub]

    jobs = {}
    with ThreadPoolExecutor(8) as pool:
        for x in xlens:
            for m in modules():
                jobs[(m, x)] = pool.submit(measure, pkg + core_files, x, m, m in TOPS, workdir)
            jobs[("core_without_muldiv", x)] = pool.submit(measure, nostub_files, x, "core", True, workdir)
        return {f"{m}_{x}": f.result() for (m, x), f in jobs.items()}


def table(results, xlens):
    names = [m for m in modules() if m not in TOPS]
    names.sort(key=lambda m: -results[f"{m}_{xlens[0]}"]["lut"])
    head = "| Module | " + " | ".join(f"LUT6 / FF / cells, XLEN = {x}" for x in xlens) + " |"
    lines = [head, "|---|" + "---|" * len(xlens)]
    for m in names + [t for t in TOPS if f"{t}_{xlens[0]}" in results] + ["core_without_muldiv"]:
        label = {"core": "core (flat)", "core_pipe": "core_pipe (flat)",
                 "core_without_muldiv": "core (flat), muldiv stubbed"}.get(m, m)
        cells = [f"{r['lut']} / {r['ff']} / {r['cells']}" for r in (results[f"{m}_{x}"] for x in xlens)]
        lines.append(f"| `{label}` | " + " | ".join(cells) + " |")
    totals = [sum(results[f"{m}_{x}"]["lut"] for m in names) for x in xlens]
    lines.append("| sum of the modules | " + " | ".join(str(t) for t in totals) + " |")
    return "\n".join(lines)


def draw_svg(results, date, commit, yosys, out):
    """Bar chart of LUT6 per module for XLEN = 64 and 32 (docs/img/units-area.svg)"""
    names = [m for m in modules() if m not in TOPS]
    names.sort(key=lambda m: -results[f"{m}_64"]["lut"])
    scale = 440 / max(results[f"{m}_64"]["lut"] for m in names)
    row, top = 56, 100
    height = top + row * len(names) + 130
    s64 = sum(results[f"{m}_64"]["lut"] for m in names)
    s32 = sum(results[f"{m}_32"]["lut"] for m in names)
    c64, c32 = results["core_pipe_64"], results["core_pipe_32"]
    where = f"commit {commit}" if commit else "no commit hash, an estimate"
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 940 {height}" width="940" height="{height}" '
         f'font-family="DejaVu Sans, sans-serif" font-size="13">',
         f'<rect width="940" height="{height}" fill="#ffffff"/>',
         '<text x="470" y="26" text-anchor="middle" fill="#222" font-size="16" font-weight="bold">'
         'Area of the individual modules: LUT6 (Yosys, abc -lut 6)</text>',
         f'<text x="470" y="44" text-anchor="middle" fill="#555" font-size="11">{date}, {yosys}, '
         f'each module on its own, one run; {where}</text>',
         '<rect x="200" y="64" width="14" height="10" fill="#33597f"/>'
         '<text x="220" y="73" fill="#555" font-size="11">XLEN = 64</text>',
         '<rect x="310" y="64" width="14" height="10" fill="#8aa9c9"/>'
         '<text x="330" y="73" fill="#555" font-size="11">XLEN = 32</text>']
    for i, m in enumerate(names):
        y = top + i * row
        o.append(f'<text x="190" y="{y + 14}" text-anchor="end" fill="#222" font-size="13">{m}</text>')
        for k, (x, color) in enumerate(((64, "#33597f"), (32, "#8aa9c9"))):
            d = results[f"{m}_{x}"]
            w = max(1, round(d["lut"] * scale))
            label = f'{d["lut"]}' + (f'  (+ {d["ff"]} flip-flops)' if d["ff"] else "")
            o.append(f'<rect x="200" y="{y + k * 22}" width="{w}" height="18" fill="{color}"/>'
                     f'<text x="{206 + w}" y="{y + k * 22 + 13}" fill="#222" font-size="11">{label}</text>')
    y = top + len(names) * row + 10
    notes = [
        f"Sum of the modules' LUT6: XLEN = 64: {s64}, XLEN = 32: {s32} (without the pipeline registers, forwarding and fetch queue).",
        f"Whole pipelined core (core_pipe.sv, one flat synthesis): XLEN = 64: {c64['lut']} LUT6 + {c64['ff']} flip-flops; "
        f"XLEN = 32: {c32['lut']} LUT6 + {c32['ff']} flip-flops.",
        "regfile: generic synthesis keeps 31 × XLEN flip-flops; on xc7 it maps to LUT RAM (ADR 005). muldiv: XLEN/2 + 1 or XLEN + 1 cycles (ADR 011).",
        "This is an area estimate in LUTs, not Fmax and not power: there is no timing and no place-and-route here.",
    ]
    for k, t in enumerate(notes):
        o.append(f'<text x="20" y="{y + k * 18}" fill="#333" font-size="11">{t}</text>')
    o.append("</svg>")
    Path(out).write_text("\n".join(o) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlen", type=int, nargs="+", default=[64, 32])
    ap.add_argument("--xc7", action="store_true", help="also synthesize for Xilinx 7 series with sta")
    ap.add_argument("--param", action="append", default=[], help="NAME=VALUE for core_pipe (and muldiv's MUL_BITS)")
    ap.add_argument("--release", action="store_true",
                    help="core_pipe as a release build: DEBUG = 0 (no commit trace, no stall/flush counters)")
    ap.add_argument("--commit", default="", help="commit hash to print with the result")
    ap.add_argument("--json", help="write the raw numbers to this file")
    ap.add_argument("--svg", help="write the bar chart (needs --xlen 64 32)")
    args = ap.parse_args()
    RELEASE["on"] = args.release
    CORE_PARAMS.update(p.split("=", 1) for p in args.param)
    if args.svg and sorted(args.xlen) != [32, 64]:
        sys.exit("area: --svg needs both XLEN = 64 and XLEN = 32")

    yosys = subprocess.run(["yosys", "-V"], capture_output=True, text=True, check=True).stdout.split("(")[0].strip()
    date = datetime.date.today().isoformat()
    with tempfile.TemporaryDirectory(prefix="area_") as tmp:
        results = run_all(args.xlen, Path(tmp))
        xc7 = run_xc7(args.xlen, Path(tmp)) if args.xc7 else None

    print(f"{yosys}, {date}, commit {args.commit or 'not given'}, `synth -top <module>` and `abc -lut 6`\n")
    print(table(results, args.xlen))
    if xc7:
        print("\n`synth_xilinx -family xc7 -abc9`, `sta`: logic-only arrival time, no routing\n")
        print(table_xc7(xc7, args.xlen))
    if args.json:
        Path(args.json).write_text(json.dumps({"date": date, "commit": args.commit, "yosys": yosys,
                                               "results": results, "xc7": xc7}, indent=1))
    if args.svg:
        draw_svg(results, date, args.commit, yosys, args.svg)
        print(f"\nwrote {args.svg}")


if __name__ == "__main__":
    main()
