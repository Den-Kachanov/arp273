# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Where the RTL lives (docs/cores/README.md): the package, the units shared by the cores, the cores"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG_DIR = "rtl/pkg"
# common: units shared by the cores; sol: the in-order core (Sol 1 and Sol 2 are configurations of it);
# ref: the single-cycle reference core. New cores (spica-naos, proxima) get their own folders here
CORE_DIRS = ("rtl/common", "rtl/sol", "rtl/ref")


def files(repo: Path = REPO, with_pkg: bool = False) -> list[Path]:
    """the SystemVerilog files of the cores, the package first when with_pkg"""
    out = sorted((repo / PKG_DIR).glob("*.sv")) if with_pkg else []
    for d in CORE_DIRS:
        out += sorted((repo / d).glob("*.sv"))
    return out


def configs(repo: Path = REPO) -> dict[str, dict[str, int]]:
    """the generations and variants of Sol as parameter sets of rtl/sol/core_pipe.sv (ADR 015)"""
    return json.loads((repo / "rtl" / "sol" / "configs.json").read_text())


if __name__ == "__main__":
    # `rtl_files.py params sol1` prints the parameters of a configuration for make: MUL_BITS=1 BP=0
    if len(sys.argv) == 3 and sys.argv[1] == "params":
        c = configs()
        if sys.argv[2] not in c:
            sys.exit(f"no configuration {sys.argv[2]}; there are: {', '.join(c)}")
        print(" ".join(f"{k}={v}" for k, v in c[sys.argv[2]].items()))
    else:
        sys.exit("usage: rtl_files.py params <configuration>")
