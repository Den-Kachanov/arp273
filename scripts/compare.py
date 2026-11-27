#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Compare a core commit log with a `spike --log-commits` log

Usage: compare.py core.log spike.log [--context N] [--hart H]

Both logs are normalized to (pc, instruction, register write, store). Spike boot ROM
instructions (pc below 0x8000_0000) are skipped. Loads are compared by their register
write, stores by address and value (an AMO by the value it writes). With several harts (spike -p2,
the SoC harness) --hart picks the lines of one hart. The comparison stops at the first difference and
prints a few lines before it; it passes when the whole core log matches the start of
the Spike log
"""

import argparse
import re
import sys

LINE = re.compile(r"core\s+(\d+):\s+\d+\s+0x([0-9a-f]+)\s+\(0x([0-9a-f]+)\)(.*)")
REG = re.compile(r"\bx(\d+)\s+0x([0-9a-f]+)")
MEM = re.compile(r"\bmem\s+0x([0-9a-f]+)(?:\s+0x([0-9a-f]+))?")
RAM_BASE = 0x80000000


def parse(path, hart=0):
    """
    Read a commit log, return a list of (pc, inst, reg, store) tuples

    reg is (number, value) or None; store is (address, value) or None
    """
    entries = []
    with open(path) as f:
        for text in f:
            m = LINE.match(text)
            if not m or int(m.group(1)) != hart:
                continue
            pc, inst, rest = int(m.group(2), 16), int(m.group(3), 16), m.group(4)
            if pc < RAM_BASE:
                continue
            reg = REG.search(rest)
            # an AMO has two: the read ("mem A") and the write ("mem A V"); the write is compared
            mem = next((x for x in MEM.findall(rest) if x[1]), None)
            store = (int(mem[0], 16), int(mem[1], 16)) if mem else None
            entries.append((pc, inst, (int(reg.group(1)), int(reg.group(2), 16)) if reg else None, store))
    return entries


def fmt(e):
    """One normalized entry as text"""
    pc, inst, reg, store = e
    s = f"pc {pc:#018x} inst {inst:#010x}"
    if reg:
        s += f" x{reg[0]}={reg[1]:#x}"
    if store:
        s += f" store [{store[0]:#x}]={store[1]:#x}"
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("core_log")
    ap.add_argument("spike_log")
    ap.add_argument("--context", type=int, default=5, help="lines to show before the first difference")
    ap.add_argument("--hart", type=int, default=0, help="compare the instructions of this hart")
    args = ap.parse_args()

    try:
        core = parse(args.core_log, args.hart)
        spike = parse(args.spike_log, args.hart)
    except FileNotFoundError as err:
        print(f"compare: {err.filename} not found")
        return 2
    if not core:
        print("compare: the core log has no instructions")
        return 1
    if not spike:
        print("compare: the Spike log has no instructions at or above 0x80000000")
        return 1

    for i, c in enumerate(core):
        if i >= len(spike):
            print(f"compare: Spike log ends after {len(spike)} instructions, the core has {len(core)}")
            return 1
        if c != spike[i]:
            print(f"compare: first difference at instruction {i}")
            for j in range(max(0, i - args.context), i):
                print(f"   {j:6d}  {fmt(core[j])}")
            print(f"  core {i:6d}  {fmt(c)}")
            print(f"  spike{i:6d}  {fmt(spike[i])}")
            return 1
    print(f"compare: OK, {len(core)} instructions match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
