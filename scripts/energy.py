# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Estimate the switching energy of a core on benchmarks, from its gate netlist (ADR 016).

1. Yosys: the release build (DEBUG = 0, plus --param values) flattened and mapped to CMOS gates
   (abc -g cmos2, as for the floorplan); every net gets a plain name, so that the Verilog netlist and
   the JSON netlist (for the fanout) name it the same way.
2. Verilator: the netlist with the core harness and --coverage-toggle, which counts the 0->1 and 1->0
   transitions of every net bit.
3. Energy, in relative units (no capacitances, no leakage):
     logic = sum over net bits of transitions * (fanout + 1)    fanout = cell inputs the bit drives
     clock = CLOCK_WEIGHT * flip-flops * cycles                 no clock gating
   CLOCK_WEIGHT = 2: two clock edges per cycle, each driving one flip-flop input.
4. Check: bit 0 of the cycle counter must toggle once per cycle and bit 1 every second cycle; if not,
   the counting is broken and the script fails.

CoreMark is run with 1 and 2 iterations; one iteration = run 2 - run 1, so start-up and the report
cancel. The gate-level simulation with toggle counting runs at about 2 thousand cycles per second, so
the benchmarks used here are short ones. The energy-delay product per iteration (energy * cycles) decides between designs.
Other benchmarks are whole programs from build/bench (scripts/bench.py --build-only).
"""

import argparse
import concurrent.futures as cf
import datetime
import fcntl
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import bench
import rtl_files

CLOCK_WEIGHT = 2
# the clock and reset distribution: counted by the clock term, not as logic (their nets reach every
# flip-flop, so as logic they would count the clock a second time)
CLOCK_NETS = {"clk_i", "rst_ni"}
# the coverage file must name nearly every net bit of the netlist, and nearly nothing else
MIN_COVERED = 0.95
MAX_UNKNOWN = 0.01
CM_FILES = ["core_list_join.c", "core_main.c", "core_matrix.c", "core_state.c", "core_util.c"]
CFLAGS = ["-march=rv64im_zicsr", "-mabi=lp64", "-mcmodel=medany", "-O2", "-ffreestanding", "-nostdlib",
          "-nostartfiles", "-fno-common"]
COV_RE = re.compile(r"\x01o\x02(.*?):(?:0->1|1->0)\x01.*' (\d+)$")


def netlist(params, out):
    """gate netlist of the release core; returns (verilog path, transistors, flip-flops, load, bit_of)"""
    rtl = [str(p.relative_to(rtl_files.REPO)) for p in rtl_files.files(with_pkg=True)]
    chparam = " ".join(f"-chparam {k} {v}" for k, v in params.items())
    script = (f"read_verilog -sv -DXLEN=64 {' '.join(rtl)}; "
              f"hierarchy -top core_pipe -chparam DEBUG 0 {chparam}; synth -top core_pipe -flatten; "
              f"abc -g cmos2; opt_clean -purge; rename -enumerate -pattern n%; "
              f"tee -q -o {out}/stat.txt stat -tech cmos; write_verilog -noattr {out}/net.v; "
              f"write_json {out}/net.json")
    subprocess.run(["yosys", "-q", "-p", script], check=True, capture_output=True)
    return (out / "net.v", *parse_netlist(out))


def parse_netlist(out):
    """(transistors, flip-flops, load by bit, bit by coverage name) from stat.txt and net.json in out"""
    stat = (out / "stat.txt").read_text()
    m = re.search(r"transistors:\s+(\d+)", stat)
    if not m:
        sys.exit("energy: no transistor count in the Yosys statistics")
    transistors = int(m.group(1))
    mod = json.loads((out / "net.json").read_text())["modules"]["core_pipe"]
    load = defaultdict(int)  # bit id -> cell inputs it drives
    ffs = 0
    for cell in mod["cells"].values():
        if "DFF" in cell["type"]:
            ffs += 1
        for port, bits in cell["connections"].items():
            if cell["port_directions"][port] == "input":
                for b in bits:
                    if isinstance(b, int):
                        load[b] += 1
    for port in mod["ports"].values():  # an output port drives the pin outside
        if port["direction"] == "output":
            for b in port["bits"]:
                if isinstance(b, int):
                    load[b] += 1
    # coverage names: "name" for one bit, "name[i]" otherwise (i from the net's offset)
    bit_of = {}
    for name, net in mod["netnames"].items():
        bits, off = net["bits"], net.get("offset", 0)
        for i, b in enumerate(bits):
            # a bit tied to "0" or "1" is known but has no net: None, and no energy
            bit_of[name if len(bits) == 1 else f"{name}[{i + off}]"] = b if isinstance(b, int) else None
    add_reg_aliases((out / "net.v").read_text(), bit_of)
    return transistors, ffs, load, bit_of


def add_reg_aliases(verilog_text, bit_of):
    """write_verilog gives some flip-flops a reg of their own (n123) and assigns it to the netlist's net
    (assign mem_cause_q[1] = n123;): the reg is another name of that bit"""
    for net, reg in re.findall(r"^\s*assign (\\\S+ \[\d+\]|\S+) = (n\d+);", verilog_text, re.MULTILINE):
        net = net.lstrip("\\").replace(" [", "[")  # escaped identifier: \u_muldiv.div_hi [0]
        if reg not in bit_of and net in bit_of:
            bit_of[reg] = bit_of[net]


def build_sim(verilog, out, jobs):
    # the generated C++ of a full netlist is large: each compiler process can take gigabytes.
    # Always from a clean directory: an interrupted build leaves object files that make takes as current
    shutil.rmtree(out / "obj", ignore_errors=True)
    subprocess.run(["verilator", "--cc", "--exe", "--build", "-j", str(jobs), "-O2", "-Wno-fatal", "-Wno-lint",
                    "-Wno-style", "--prefix", "Vtop", "--x-assign", "unique", "--x-initial", "unique",
                    "--coverage-toggle", "--coverage-underscore", "--top-module", "core_pipe",
                    "-CFLAGS", "-DPIPE -DRELEASE", "--Mdir", str(out / "obj"), "-o", "sim", str(verilog),
                    "tb/core/main.cpp"], check=True, capture_output=True)
    return out / "obj" / "sim"


def tohost(cross, elf):
    nm = subprocess.run([f"{cross}nm", str(elf)], check=True, capture_output=True, text=True).stdout
    return next(p[0] for p in (ln.split() for ln in nm.splitlines()) if len(p) == 3 and p[2] == "tohost")


def coremark(cross, cm, iters, out):
    elf = out / f"coremark{iters}.elf"
    subprocess.run([f"{cross}gcc", *CFLAGS, *bench.libc_flags(cross), "-Isw/coremark", f"-I{cm}", "-DPERFORMANCE_RUN=1",
                    f"-DITERATIONS={iters}", '-DFLAGS_STR="energy"', "-Wl,--no-warn-rwx-segments", "-T",
                    "sw/link.ld", "sw/crt0.S", *(f"{cm}/{f}" for f in CM_FILES), "sw/coremark/core_portme.c",
                    "sw/lib/printf.c", "sw/lib/libc.c", "-o", str(elf)], check=True)
    subprocess.run([f"{cross}objcopy", "-O", "binary", str(elf), str(elf.with_suffix(".bin"))], check=True)
    return elf


def coverage_name(name):
    """the netlist's name of a coverage point: Verilator writes characters that are not allowed in a
    C++ identifier as __0 and two hex digits (fq_inst_q[0] becomes fq_inst_q__05b0__05d)"""
    return re.sub(r"__0([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), name)


def read_coverage(cov):
    """transitions (0->1 plus 1->0) per net bit name, as the netlist names it"""
    counts = defaultdict(int)
    for line in cov.read_text().splitlines():
        mm = COV_RE.search(line)
        if mm:
            counts[coverage_name(mm.group(1))] += int(mm.group(2))
    return counts


def run(sim, elf, cov, cross, max_cycles):
    r = subprocess.run([str(sim), str(elf.with_suffix(".bin")), "/dev/null", tohost(cross, elf), str(max_cycles)],
                       capture_output=True, text=True, env={"COVERAGE_FILE": str(cov), "PATH": "/usr/bin"})
    m = re.search(r"cycles (\d+) instret (\d+)", r.stderr)
    if r.returncode != 0 or not m:
        sys.exit(f"energy: {elf.name} failed (exit {r.returncode}): {r.stderr.strip()[-300:]}")
    return int(m.group(1)), int(m.group(2)), read_coverage(cov)


def energy(cycles, counts, ffs, load, bit_of):
    per_bit = {}
    for name, n in counts.items():  # a bit with several names (port and wire) counts once
        b = bit_of.get(name)
        if b is not None and name not in CLOCK_NETS:
            per_bit[b] = max(per_bit.get(b, 0), n)
    logic = sum(n * (load.get(b, 0) + 1) for b, n in per_bit.items())
    clock = CLOCK_WEIGHT * ffs * cycles
    return {"cycles": cycles, "toggle_load": logic, "clock": clock, "energy": logic + clock,
            "covered_bits": len(per_bit)}


def _try_lock(f):
    """take the lock if it is free; False if another run holds it"""
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def check_coverage(name, cycles, counts, bit_of):
    """
    The counting works: bit 0 of the cycle counter toggles every cycle and bit 1 every second one; the
    coverage file names nearly every net bit of the netlist (without --coverage-underscore Verilator
    skips most of them) and nearly no name the netlist does not have (the two netlists would differ)
    """
    t0, t1 = counts.get("perf_cycles_o[0]", -1), counts.get("perf_cycles_o[1]", -1)
    if abs(t0 - cycles) > 2 or abs(t1 - cycles / 2) > 2:
        sys.exit(f"energy: toggle count check failed on {name}: cycles {cycles}, "
                 f"perf_cycles_o[0] {t0}, perf_cycles_o[1] {t1}")
    covered = sum(1 for n in bit_of if n in counts) / len(bit_of)
    unknown = sum(1 for n in counts if n not in bit_of) / max(len(counts), 1)
    if covered < MIN_COVERED or unknown > MAX_UNKNOWN:
        sys.exit(f"energy: coverage check failed on {name}: {covered:.1%} of the net bits covered "
                 f"(need {MIN_COVERED:.0%}), {unknown:.1%} of the coverage names unknown "
                 f"(at most {MAX_UNKNOWN:.0%})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--param", action="append", default=[], help="NAME=VALUE for core_pipe (repeatable)")
    ap.add_argument("--bench", action="append", default=[], help="benchmark from build/bench, or coremark")
    ap.add_argument("--coremark", default="third_party/coremark")
    ap.add_argument("--bench-dir", default="build/bench")
    ap.add_argument("--out", default="build/energy")
    ap.add_argument("--cross", default="riscv64-elf-")
    ap.add_argument("--max-cycles", type=int, default=400_000_000)
    ap.add_argument("--core", default="")
    ap.add_argument("--commit", default="")
    ap.add_argument("--json")
    ap.add_argument("-j", type=int, default=4, help="simulations run in parallel")
    ap.add_argument("--reuse", action="store_true",
                    help="no synthesis and no simulation: recompute from net.json, stat.txt and the .dat files "
                         "of an earlier run of the same configuration")
    ap.add_argument("--no-wait", action="store_true", help="do not wait for other runs (not recommended)")
    ap.add_argument("--build-jobs", type=int, default=4, help="compiler processes for the netlist build")
    args = ap.parse_args()
    params = dict(p.split("=", 1) for p in args.param)
    tag = "_".join(f"{k}{v}" for k, v in sorted(params.items())) or "default"
    out = Path(args.out) / tag
    out.mkdir(parents=True, exist_ok=True)

    # one estimate at a time: a run builds a large netlist simulation and starts several copies of it,
    # and a few runs side by side ran a 30 GB machine out of memory. A second run waits here
    lock = open(Path(args.out) / ".lock", "w")
    if not args.no_wait:
        print(f"energy: {tag}: waiting for other runs to finish" if not _try_lock(lock) else "", end="")
        fcntl.flock(lock, fcntl.LOCK_EX)

    if args.reuse:
        transistors, ffs, load, bit_of = parse_netlist(out)
    else:
        verilog, transistors, ffs, load, bit_of = netlist(params, out)
        sim = build_sim(verilog, out, args.build_jobs)
    runs = {}
    for b in args.bench or ["coremark"]:
        if b == "coremark":
            runs["coremark1"] = out / "coremark1.elf" if args.reuse else coremark(args.cross, args.coremark, 1, out)
            runs["coremark2"] = out / "coremark2.elf" if args.reuse else coremark(args.cross, args.coremark, 2, out)
        else:
            runs[b] = Path(args.bench_dir) / f"{b}.elf"
    if args.reuse:  # cycles = toggles of bit 0 of the cycle counter (the check below confirms it)
        done = {}
        for k in runs:
            counts = read_coverage(out / f"{k}.dat")
            done[k] = (counts["perf_cycles_o[0]"], 0, counts)
    else:
        with cf.ThreadPoolExecutor(args.j) as ex:
            done = dict(zip(runs, ex.map(lambda kv: run(sim, kv[1], out / f"{kv[0]}.dat", args.cross,
                                                        args.max_cycles), runs.items()), strict=True))

    results = {}
    for name, (cycles, instret, counts) in done.items():
        check_coverage(name, cycles, counts, bit_of)
        results[name] = energy(cycles, counts, ffs, load, bit_of) | {"instret": instret}
    if "coremark1" in results:
        c1, c2 = results["coremark1"], results["coremark2"]
        it = {k: c2[k] - c1[k] for k in ("cycles", "toggle_load", "clock", "energy")}
        it["edp"] = it["energy"] * it["cycles"]
        results["coremark_iteration"] = it

    print(f"core_pipe {tag}: {transistors} transistors (CMOS estimate), {ffs} flip-flops, "
          f"{len(bit_of)} net bits")
    print(f"{'run':20s} {'cycles':>11s} {'logic':>14s} {'clock':>14s} {'energy':>14s}  per cycle")
    for name, r in results.items():
        line = (f"{name:20s} {r['cycles']:11.0f} {r['toggle_load']:14.0f} {r['clock']:14.0f} "
                f"{r['energy']:14.0f}  {r['energy'] / r['cycles']:9.0f}")
        if "edp" in r:
            line += f"   energy x cycles {r['edp']:.4g}"
        print(line)
    if args.json:
        meta = {"core": args.core, "commit": args.commit, "date": datetime.date.today().isoformat(),
                "params": params, "transistors": transistors, "flip_flops": ffs, "clock_weight": CLOCK_WEIGHT,
                "units": "relative: transitions x (fanout + 1), plus clock"}
        Path(args.json).write_text(json.dumps({"meta": meta, "results": results}, indent=1) + "\n")


if __name__ == "__main__":
    main()
