# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Build and run the benchmark suites on a core simulation; print cycles of the measured part.

Suites (each from a git submodule; only its sources are compiled, the support code is ours):
  coremark   CoreMark (third_party/coremark), port in sw/coremark
  rt         riscv-tests benchmarks (third_party/riscv-tests/benchmarks), support in sw/bench/rt_support.c
  embench    Embench-IoT 1.0 (third_party/embench-iot), board support in sw/bench/embench

The cores have no floating point. Without --softfp only the integer benchmarks are built; with
--softfp <library> (soft-float arithmetic and the few libm functions, built for rv64im/lp64) the
floating-point ones are added too, and they measure floating point emulated in software. Every benchmark checks its own result; a run counts only if its exit
code is 0. The measured part is what the benchmark times itself (start/stop around the kernel).

--json writes the results; --baseline compares with an earlier JSON: the ratio of measured cycles per
benchmark (baseline / now, above 1 is faster) and their geometric mean.
"""

import argparse
import concurrent.futures as cf
import datetime
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ARCH = ["-march=rv64im_zicsr", "-mabi=lp64", "-mcmodel=medany"]
BASE_CFLAGS = ARCH + ["-O2", "-ffreestanding", "-nostdlib", "-nostartfiles", "-fno-common"]
LINK = ["-Wl,--no-warn-rwx-segments", "-T", "sw/link.ld", "sw/crt0.S"]
LIB = ["sw/lib/printf.c", "sw/lib/libc.c"]

RT_BENCH = ["dhrystone", "median", "memcpy", "multiply", "qsort", "rsort", "towers", "vvadd"]
RT_CFLAGS = ["-std=gnu99", "-fno-builtin-printf", "-fno-tree-loop-distribute-patterns", "-fpermissive",
             "-Wno-implicit-int", "-Wno-implicit-function-declaration", "-DPREALLOCATE=1"]
EMBENCH = ["aha-mont64", "crc32", "edn", "huffbench", "matmult-int", "nettle-aes", "nettle-sha256",
           "nsichneu", "picojpeg", "qrduino", "sglib-combined", "slre"]
EMBENCH_FP = ["cubic", "minver", "nbody", "st", "statemate", "ud", "wikisort"]
RT_BENCH_FP = ["spmv"]
EB_CFLAGS = ["-std=gnu11", "-DHAVE_CONFIG_H", "-DHAVE_BOARDSUPPORT_H"]
CM_FILES = ["core_list_join.c", "core_main.c", "core_matrix.c", "core_state.c", "core_util.c"]


def build_commands(args):
    """name -> (suite, gcc arguments without -o)"""
    out = {}
    cm = Path(args.coremark)
    if "coremark" in args.suites:
        out["coremark"] = ("coremark", BASE_CFLAGS + [
            "-Isw/coremark", f"-I{cm}", "-DPERFORMANCE_RUN=1", f"-DITERATIONS={args.cm_iter}",
            f'-DFLAGS_STR="{" ".join(BASE_CFLAGS)}"'] + LINK +
            [str(cm / f) for f in CM_FILES] + ["sw/coremark/core_portme.c"] + LIB)
    rt = Path(args.riscv_tests) / "benchmarks"
    fp = [args.softfp] if args.softfp else []
    if "rt" in args.suites:
        for b in RT_BENCH + (RT_BENCH_FP if fp else []):
            out[f"rt-{b}"] = ("rt", BASE_CFLAGS + RT_CFLAGS + [
                f"-I{rt.parent / 'env'}", f"-I{rt / 'common'}", f"-I{rt / b}"] + LINK +
                sorted(str(p) for p in (rt / b).glob("*.c")) + ["sw/bench/rt_support.c"] + LIB + fp)
    eb = Path(args.embench)
    if "embench" in args.suites:
        for b in EMBENCH + (EMBENCH_FP if fp else []):
            out[f"eb-{b}"] = ("embench", BASE_CFLAGS + EB_CFLAGS + [
                "-Isw/bench/embench", f"-I{eb / 'support'}", f"-I{eb / 'src' / b}"] + LINK +
                [str(eb / "support" / "main.c"), str(eb / "support" / "beebsc.c")] +
                sorted(str(p) for p in (eb / "src" / b).glob("*.c")) + ["sw/bench/embench/board.c"] + LIB + fp)
    return out


def run_one(name, suite, gcc_args, args):
    """build one benchmark and (unless --build-only) run it; returns (name, result)"""
    out = Path(args.out)
    elf, binf = out / f"{name}.elf", out / f"{name}.bin"
    cc = subprocess.run([f"{args.cross}gcc", *gcc_args, "-o", str(elf)], capture_output=True, text=True)
    if cc.returncode != 0:
        return name, {"error": "build failed: " + cc.stderr.strip().splitlines()[-1]}
    subprocess.run([f"{args.cross}objcopy", "-O", "binary", str(elf), str(binf)], check=True)
    if args.build_only:
        return name, {"built": True}
    nm = subprocess.run([f"{args.cross}nm", str(elf)], check=True, capture_output=True, text=True).stdout
    tohost = next(p[0] for p in (ln.split() for ln in nm.splitlines()) if len(p) == 3 and p[2] == "tohost")
    run = subprocess.run([args.sim, str(binf), "/dev/null", tohost, str(args.max_cycles)],
                         capture_output=True, text=True)
    report = run.stdout
    res: dict[str, float]
    if suite == "coremark":
        ticks = re.search(r"^Total ticks\s*:\s*(\d+)", report, re.MULTILINE)
        iters = re.search(r"^Iterations\s*:\s*(\d+)", report, re.MULTILINE)
        if (ticks is None or iters is None or run.returncode != 0
                or "2K performance run parameters" not in report
                or re.search(r"ERROR! (list|matrix|state) crc", report)):
            return name, {"error": "CoreMark did not validate"}
        res = {"cycles": int(ticks.group(1)), "iterations": int(iters.group(1))}
        res["coremark_per_mhz"] = res["iterations"] * 1e6 / res["cycles"]
    else:
        m = re.search(r"bench: measured cycles (\d+) instret (\d+)", report)
        if run.returncode != 0 or not m:
            return name, {"error": f"exit code {run.returncode} (wrong result or no report)"}
        res = {"cycles": int(m.group(1)), "instret": int(m.group(2))}
        res["cpi"] = res["cycles"] / res["instret"]
        d = re.search(r"Dhrystones per Second:\s*(\d+)", report)
        if d:  # Dhrystone's clock is mcycle with HZ = 10^6: runs per million cycles
            res["dmips_per_mhz"] = int(d.group(1)) / 1757
    return name, res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", required=True)
    ap.add_argument("--cross", default="riscv64-elf-")
    ap.add_argument("--coremark", default="third_party/coremark")
    ap.add_argument("--riscv-tests", default="third_party/riscv-tests")
    ap.add_argument("--embench", default="third_party/embench-iot")
    ap.add_argument("--softfp", help="soft-float library: adds the floating-point benchmarks")
    ap.add_argument("--suites", nargs="+", default=["coremark", "rt", "embench"])
    ap.add_argument("--cm-iter", type=int, default=10)
    ap.add_argument("--max-cycles", type=int, default=400_000_000)
    ap.add_argument("--out", default="build/bench")
    ap.add_argument("--json")
    ap.add_argument("--baseline", help="earlier --json output to compare with")
    ap.add_argument("--core", default="", help="name of the core for the JSON, for example 'Sol 1'")
    ap.add_argument("--commit", default="", help="commit of the RTL, recorded in the JSON")
    ap.add_argument("-j", type=int, default=8)
    ap.add_argument("--build-only", action="store_true", help="only build the programs (for scripts/energy.py)")
    args = ap.parse_args()
    Path(args.out).mkdir(parents=True, exist_ok=True)

    cmds = build_commands(args)
    with cf.ThreadPoolExecutor(args.j) as ex:
        results = dict(ex.map(lambda kv: run_one(kv[0], kv[1][0], kv[1][1], args), cmds.items()))
    if args.build_only:
        failed = [n for n, r in results.items() if "error" in r]
        for n in failed:
            print(f"{n}: {results[n]['error']}")
        sys.exit(1 if failed else 0)

    base = json.loads(Path(args.baseline).read_text())["results"] if args.baseline else {}
    print(f"{'benchmark':22s} {'cycles':>12s} {'CPI':>6s}  {'vs baseline':>11s}  note")
    ratios = []
    failed = 0
    for name in cmds:
        r = results[name]
        if "error" in r:
            failed += 1
            print(f"{name:22s} {'FAILED':>12s}         {'':11s}  {r['error']}")
            continue
        note = "soft-float" if name.partition("-")[2] in EMBENCH_FP + RT_BENCH_FP else ""
        if "coremark_per_mhz" in r:
            note = f"CoreMark/MHz {r['coremark_per_mhz']:.3f}"
        if "dmips_per_mhz" in r:
            note = f"DMIPS/MHz {r['dmips_per_mhz']:.3f}"
        cmp = ""
        if name in base and "cycles" in base[name]:
            ratio = base[name]["cycles"] / r["cycles"]
            ratios.append(ratio)
            cmp = f"{ratio:.3f}x"
        cpi = f"{r['cpi']:.3f}" if "cpi" in r else ""
        print(f"{name:22s} {r['cycles']:12d} {cpi:>6s}  {cmp:>11s}  {note}")
    if ratios:
        print(f"geometric mean of speedups over {len(ratios)} benchmarks: "
              f"{math.exp(sum(map(math.log, ratios)) / len(ratios)):.3f}x")
    if args.json:
        gcc = subprocess.run([f"{args.cross}gcc", "--version"], capture_output=True, text=True).stdout
        meta = {"core": args.core, "commit": args.commit, "date": datetime.date.today().isoformat(),
                "sim": args.sim, "memory": "BRAM (1-cycle)" if not os.environ.get("MEM_STALL") else
                f"MEM_STALL={os.environ['MEM_STALL']}", "gcc": gcc.splitlines()[0] if gcc else "",
                "cflags": " ".join(BASE_CFLAGS), "softfp": bool(args.softfp)}
        Path(args.json).write_text(json.dumps({"meta": meta, "results": results}, indent=1) + "\n")
    print("(simulation; not official CoreMark, Dhrystone or Embench scores)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
