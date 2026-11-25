# Third-party software

The code in this repository is written from scratch and is licensed under CERN-OHL-S-2.0.
Nothing below is copied into the repository; these are tools that are installed or fetched separately.

| Component | Use | License |
|---|---|---|
| Verilator | simulation, lint | LGPL-3.0-only OR Artistic-2.0 |
| Yosys | synthesis estimates | ISC |
| cocotb | unit tests | BSD-3-Clause |
| pytest | test runner | MIT |
| Spike (riscv-isa-sim) | reference model | BSD-3-Clause |
| GNU RISC-V toolchain (gcc, binutils) | software build | GPL-3.0-or-later |
| gitleaks | secret scanning (pre-commit, CI) | MIT |
| netlistsvg (npm, pinned in `tools/package-lock.json`, not committed) | schematics from the Yosys netlist | MIT |
| elkjs (dependency of netlistsvg) | graph layout for netlistsvg | EPL-1.0 |
| Graphviz | layout of the flattened core (`make schematics M=core LEVEL=flat`) | EPL-1.0 |
| python-igraph (pip, pinned in `requirements.txt`) | DrL layout of large netlists (graph level) | GPL-2.0-or-later |
| riscv-tests and riscv-test-env (git submodule `third_party/riscv-tests`, not a copy) | ISA tests `rv64ui-p`, `rv64um-p` (`make riscv-tests`) | BSD-3-Clause |

Planned, not yet used: RISCOF and riscv-arch-test.
Check the license of each before it is added, and add test data (for example MNIST) here too.
