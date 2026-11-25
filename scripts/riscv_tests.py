#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Build and run the riscv-tests ISA suites (physical-memory environment "p") on the pipelined core

Each test is self-checking: it ends with ECALL, its trap handler writes tohost (1 = pass,
(n << 1) | 1 = test case n failed). The harness runs with TRAP_STOP=0, so the core takes the
exceptions itself (ADR 014). The tests are compiled from the sources in the riscv-tests git
submodule; nothing of it is copied into this repository.

Not run: ma_data (misaligned loads and stores must work, in hardware or emulated by a handler;
the core traps on them, which the specification allows; see ADR 014)

Usage: riscv_tests.py --sim build/core_pipe_sim [--src third_party/riscv-tests] [--suites rv64ui rv64um]
                      [--cross riscv64-elf-] [-j N]
"""

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKIP = {"ma_data": "misaligned data access is not supported (traps)"}
ARCH = {"rv64ui": "rv64i_zicsr_zifencei", "rv64um": "rv64im_zicsr_zifencei"}
GCC_OPTS = ["-mabi=lp64", "-static", "-mcmodel=medany", "-fvisibility=hidden", "-nostdlib", "-nostartfiles"]


def build(src, cross, out, suite, test):
    """Compile one test into ELF and flat binary; return (binary, tohost address in hex)"""
    elf = out / f"{suite}-p-{test}.elf"
    binf = elf.with_suffix(".bin")
    cmd = [f"{cross}gcc", f"-march={ARCH[suite]}", *GCC_OPTS,
           f"-I{src}/env/p", f"-I{src}/isa/macros/scalar", f"-T{src}/env/p/link.ld",
           str(src / "isa" / suite / f"{test}.S"), "-o", str(elf)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    subprocess.run([f"{cross}objcopy", "-O", "binary", str(elf), str(binf)], check=True)
    nm = subprocess.run([f"{cross}nm", str(elf)], check=True, capture_output=True, text=True).stdout
    tohost = next(line.split()[0] for line in nm.splitlines() if line.split()[-1] == "tohost")
    return binf, tohost


def run(sim, src, cross, out, suite, test):
    """Return (name, passed, detail)"""
    name = f"{suite}-p-{test}"
    try:
        binf, tohost = build(src, cross, out, suite, test)
    except subprocess.CalledProcessError as e:
        lines = (e.stderr or "").strip().splitlines()
        return name, False, "build failed: " + (lines[-1] if lines else f"status {e.returncode}")
    env = dict(os.environ, TRAP_STOP="0")
    r = subprocess.run([str(sim), str(binf), str(out / f"{name}.log"), tohost, "200000"],
                       capture_output=True, text=True, env=env)
    info = {line.split()[0]: line for line in r.stderr.splitlines() if line}
    cycles = info.get("cycles", "").split(" stalls")[0]
    if r.returncode == 0:
        return name, True, cycles
    if r.returncode < 100:
        return name, False, f"test case {r.returncode} failed ({cycles})"
    lines = r.stderr.strip().splitlines()
    return name, False, lines[0] if lines else f"status {r.returncode}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--sim", required=True, type=Path)
    ap.add_argument("--src", default="third_party/riscv-tests", type=Path)
    ap.add_argument("--suites", nargs="+", default=["rv64ui", "rv64um"])
    ap.add_argument("--cross", default="riscv64-elf-")
    ap.add_argument("--out", default="build/riscv-tests", type=Path)
    ap.add_argument("-j", type=int, default=os.cpu_count() or 4)
    a = ap.parse_args()

    if not (a.src / "isa").is_dir() or not (a.src / "env" / "p").is_dir():
        sys.exit(f"riscv-tests not found in {a.src}: git submodule update --init --recursive")
    a.out.mkdir(parents=True, exist_ok=True)

    jobs = []
    skipped = 0
    for suite in a.suites:
        tests = sorted(p.stem for p in (a.src / "isa" / suite).glob("*.S"))
        for t in tests:
            if t in SKIP:
                print(f"  skip  {suite}-p-{t}: {SKIP[t]}")
                skipped += 1
            else:
                jobs.append((suite, t))

    with ThreadPoolExecutor(a.j) as ex:
        results = list(ex.map(lambda st: run(a.sim, a.src, a.cross, a.out, *st), jobs))
    failed = 0
    for name, ok, detail in results:
        print(f"  {'pass' if ok else 'FAIL'}  {name}: {detail}")
        failed += not ok
    print(f"riscv-tests: {len(results) - failed}/{len(results)} passed, {skipped} skipped")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
