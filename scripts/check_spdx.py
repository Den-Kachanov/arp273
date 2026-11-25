# SPDX-License-Identifier: CERN-OHL-S-2.0
"""
Check that every source file starts with the SPDX license line

Usage: check_spdx.py

Checked: .sv .py .S .c .h .cpp .sh .ld, Makefile. The identifier must be on one of the first
3 lines (a shebang may come first). Directories that are not ours are skipped
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ID = "SPDX-License-Identifier: CERN-OHL-S-2.0"
SUFFIXES = {".sv", ".py", ".S", ".c", ".h", ".cpp", ".sh", ".ld"}
SKIP = {".venv", "build", "sim_build", "obj_dir", "__pycache__", ".git", "third_party", ".pytest_cache"}


def sources():
    for p in sorted(REPO.rglob("*")):
        if not p.is_file() or SKIP & set(p.relative_to(REPO).parts):
            continue
        if p.suffix in SUFFIXES or p.name == "Makefile":
            yield p


def main():
    missing = [p for p in sources() if ID not in "".join(p.read_text(errors="replace").splitlines(True)[:3])]
    for p in missing:
        print(f"missing SPDX line: {p.relative_to(REPO)}")
    print(f"spdx: {'FAILED' if missing else 'OK'}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
