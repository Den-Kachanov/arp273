#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Check that every source file starts with the SPDX license and copyright lines (REUSE style)

Usage: check_spdx.py

Checked: .sv .py .S .c .h .cpp .sh .ld .yml, Makefile. Both lines must be among the first 4
(a shebang may come first):
    SPDX-License-Identifier: CERN-OHL-S-2.0
    SPDX-FileCopyrightText: <year> Denys Kachanov
Directories that are not ours are skipped
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ID = "SPDX-License-Identifier: CERN-OHL-S-2.0"
COPYRIGHT = re.compile(r"SPDX-FileCopyrightText: \d{4}(-\d{4})? Denys Kachanov")
SUFFIXES = {".sv", ".py", ".S", ".c", ".h", ".cpp", ".sh", ".ld", ".yml"}
SKIP = {".venv", "build", "sim_build", "obj_dir", "__pycache__", ".git", "third_party", ".pytest_cache", "node_modules"}


def sources():
    for p in sorted(REPO.rglob("*")):
        if not p.is_file() or SKIP & set(p.relative_to(REPO).parts):
            continue
        if p.suffix in SUFFIXES or p.name == "Makefile":
            yield p


def main():
    missing = []
    for p in sources():
        head = "".join(p.read_text(errors="replace").splitlines(True)[:4])
        for what, ok in (("license", ID in head), ("copyright", bool(COPYRIGHT.search(head)))):
            if not ok:
                missing.append(p)
                print(f"missing SPDX {what} line: {p.relative_to(REPO)}")
    print(f"spdx: {'FAILED' if missing else 'OK'}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
