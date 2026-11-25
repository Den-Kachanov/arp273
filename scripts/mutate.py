#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Mutation check of the unit tests

Usage: mutate.py <name> [--jobs N]        (list of mutants: scripts/mutants/<name>.py)

A copy of rtl/ and tb/ is made in a temporary directory. Each mutant is one textual change
of one RTL file; the tests of that module run against the changed copy. A mutant is
"killed" when the tests fail. A survivor is either a gap in the tests or an equivalent
mutant (a change that cannot alter behaviour); equivalent mutants are listed in the file
with the reason, anything else that survives makes the script exit with 1

A mutants file defines:
    RTL         RTL file to change, relative to the repo root
    TEST        test file that must kill the mutants, relative to the repo root
    KEXPR       optional pytest -k expression (for example "64" to run XLEN = 64 only)
    MUTANTS     list of (name, old text, new text); old text must occur exactly once
    EQUIVALENT  optional dict: mutant name -> why it cannot change behaviour
"""

import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# the tests need cocotb and pytest from the project virtualenv, whatever Python started this script
VENV_PYTHON = REPO / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
IGNORE = shutil.ignore_patterns("sim_build", "__pycache__", "*.pyc")


def load(name):
    """Import scripts/mutants/<name>.py"""
    path = REPO / "scripts" / "mutants" / f"{name}.py"
    if not path.exists():
        names = sorted(p.stem for p in (REPO / "scripts" / "mutants").glob("*.py"))
        sys.exit(f"mutate: no {path.name}; available: {', '.join(names)}")
    spec = importlib.util.spec_from_file_location(f"mutants_{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_one(work, mod, index, name, old, new):
    """Build a private copy with one mutant and run the tests; return (name, "killed" | "survived" | "build")"""
    copy = work / f"m{index}"
    for d in ("rtl", "tb"):
        shutil.copytree(REPO / d, copy / d, ignore=IGNORE)
    target = copy / mod.RTL
    target.write_text(target.read_text().replace(old, new))
    cmd = [PYTHON, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", str(copy / mod.TEST)]
    if getattr(mod, "KEXPR", None):
        cmd += ["-k", mod.KEXPR]
    res = subprocess.run(cmd, cwd=copy, capture_output=True, text=True)
    shutil.rmtree(copy, ignore_errors=True)
    if res.returncode == 0:
        return name, "survived"
    # a mutant that does not even build says nothing about the tests
    if "%Error" in res.stdout + res.stderr and "Exiting due to" in res.stdout + res.stderr:
        return name, "build"
    return name, "killed"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="mutants file name without .py")
    ap.add_argument("--jobs", type=int, default=2, help="mutants tested in parallel (default 2)")
    args = ap.parse_args()

    mod = load(args.name)
    source = (REPO / mod.RTL).read_text()
    equivalent = getattr(mod, "EQUIVALENT", {})

    jobs = []
    for i, (name, old, new) in enumerate(mod.MUTANTS):
        if source.count(old) != 1:
            sys.exit(f"mutate: '{name}': the old text occurs {source.count(old)} times in {mod.RTL}, expected 1")
        if old == new:
            sys.exit(f"mutate: '{name}': the mutant does not change anything")
        jobs.append((i, name, old, new))

    unexplained = []
    broken = []
    with tempfile.TemporaryDirectory(prefix="mutate_") as tmp, ThreadPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(run_one, Path(tmp), mod, *j) for j in jobs]
        killed = 0
        for f in futures:
            name, status = f.result()
            if status == "build":
                print(f"  BUILD ERROR {name}: the mutant does not compile, fix the mutant")
                broken.append(name)
            elif status == "killed":
                killed += 1
                print(f"  killed     {name}")
            elif name in equivalent:
                print(f"  equivalent {name}: {equivalent[name]}")
            else:
                print(f"  SURVIVED   {name}")
                unexplained.append(name)

    print(f"{args.name}: {len(jobs)} mutants, {killed} killed, "
          f"{len(jobs) - killed - len(unexplained) - len(broken)} equivalent, "
          f"{len(unexplained)} survived, {len(broken)} do not build")
    return 1 if unexplained or broken else 0


if __name__ == "__main__":
    sys.exit(main())
