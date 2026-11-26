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
| CoreMark (git submodule `third_party/coremark`, not a copy; only its benchmark sources are compiled, the port in `sw/coremark/` is our own) | benchmark (`make coremark`) | Apache-2.0; the CoreMark name is an EEMBC trademark under its Acceptable Use Agreement (`LICENSE.md` of the submodule): results from simulation are not called official scores |
| riscv-tests benchmarks (the same submodule, `benchmarks/`) | dhrystone, median, memcpy, multiply, qsort, rsort, towers, vvadd, spmv (`make bench`); our own support code in `sw/bench/rt_support.c` | BSD-3-Clause |
| Embench-IoT 1.0 (git submodule `third_party/embench-iot`, tag `embench-1.0`, not a copy) | 19 benchmarks, 7 of them in soft-float (`make bench`); our own board support in `sw/bench/embench/` | GPL-3.0-or-later (some benchmarks under their own licenses); compiled locally into test programs, which are not distributed |
| compiler-rt 19.1.7 (LLVM), release archive pinned by SHA-256, downloaded by `scripts/softfp.py` into `build/`, not committed | soft-float arithmetic for the floating-point benchmarks (`make bench`) | Apache-2.0 WITH LLVM-exception |
| newlib 4.5.0 (fdlibm part of libm), release archive pinned by SHA-256, downloaded the same way | `pow`, `sqrt`, `acos`, `cos`, `atan` for the floating-point benchmarks | Sun Microsystems permissive notice (fdlibm; `COPYING.NEWLIB`) |

Planned, not yet used: RISCOF and riscv-arch-test.
Check the license of each before it is added, and add test data (for example MNIST) here too.
