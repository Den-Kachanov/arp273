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

Planned, not yet used: `riscv-tests` (git submodule, not a copy), RISCOF and riscv-arch-test.
Check the license of each before it is added, and add test data (for example MNIST) here too.
