# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Run CoreMark on a core simulation and report CoreMark/MHz.

The program prints its report through the UART (stdout of the harness). One tick is one clock
cycle (mcycle), so CoreMark/MHz = iterations * 10^6 / ticks, independent of any clock frequency.
The run is accepted only if CoreMark's own check passes: a known seed set and the CRCs of the
list, matrix and state that CoreMark expects.

This is a simulation measurement: EEMBC's reporting rules ask for at least 10 seconds of run time
on real hardware, so the number is not an official CoreMark score.
"""

import argparse
import re
import subprocess
import sys


def nm_symbol(cross, elf, name):
    out = subprocess.run([f"{cross}nm", elf], check=True, capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == name:
            return parts[0]
    sys.exit(f"{name} not found in {elf}")


def field(text, name):
    m = re.search(rf"^{re.escape(name)}\s*:\s*(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sim", required=True, help="harness binary (release build recommended)")
    ap.add_argument("--elf", required=True)
    ap.add_argument("--bin", required=True)
    ap.add_argument("--cross", default="riscv64-elf-")
    ap.add_argument("--max-cycles", type=int, default=200_000_000)
    ap.add_argument("--quiet", action="store_true", help="only the summary, not CoreMark's report")
    args = ap.parse_args()

    tohost = nm_symbol(args.cross, args.elf, "tohost")
    run = subprocess.run([args.sim, args.bin, "/dev/null", tohost, str(args.max_cycles)],
                         capture_output=True, text=True)
    report, harness = run.stdout, run.stderr
    if not args.quiet:
        print(report, end="")

    ticks, iters = field(report, "Total ticks"), field(report, "Iterations")
    # CoreMark checks its own results: the seeds must be a known run and the CRCs of the list,
    # matrix and state must match its table. Its other error, "Must execute for at least 10 secs",
    # is a reporting rule (here a "second" is 10^6 cycles), not a wrong result, so it is ignored
    known = re.search(r"^2K performance run parameters", report, re.MULTILINE)
    crc_error = re.search(r"ERROR! (list|matrix|state) crc", report)
    valid = bool(known) and not crc_error and "[0]crcfinal" in report
    counters = re.search(r"cycles (\d+) instret (\d+)", harness)
    if run.returncode != 0 or not ticks or not iters or not valid:
        print(harness, file=sys.stderr)
        sys.exit("coremark: FAILED (no valid report)")

    print(f"coremark: {iters} iterations, {ticks} cycles timed, "
          f"CoreMark/MHz {iters * 1e6 / ticks:.3f} (simulation, not an official score)")
    if counters:
        cycles, instret = int(counters.group(1)), int(counters.group(2))
        print(f"coremark: whole program {cycles} cycles, {instret} instructions, CPI {cycles / instret:.3f}")


if __name__ == "__main__":
    main()
