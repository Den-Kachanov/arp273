# arp273

RV64 SoC: CPU, vector unit and SIMT GPU

> **Status: work in progress.** Currently stage 1, the RV64I core.

A learning project in SystemVerilog: a small system-on-chip built from scratch.

1. **CPU:** RV64I core, 5-stage pipeline, M-mode, caches, M extension, FPGA bring-up, CoreMark.
2. **Vector unit:** subset of RVV 1.0 (Zve32x, VLEN = 128 as a parameter), checked against Spike.
3. **GPU:** SIMT, [Vortex](https://github.com/vortexgpgpu/vortex)-style (a RISC-V core per thread with thread-control instructions), driver, MNIST and 3D cube demos. Inspired by Vortex, no code reused.

The RTL, unit tests and verification are written by the me, with no third-party RTL.

## Roadmap

- [ ] Stage 1: RV64I core, single-cycle then pipelined, `rv64ui` tests, Spike comparison
- [ ] Stage 2: CSR and exceptions, M extension, compliance, SoC, FPGA, CoreMark, 4 KB cache
- [ ] Stage 3: vector unit, Zve32x
- [ ] Stage 4: GPU specification and SIMT core in simulation
- [ ] Stage 5: integration, driver, MNIST and 3D cube demos
- [ ] Stage 6: report and write-up

Dates are plans, not promises. Items are checked off only when tests pass.

## Verification approach

- Module tests with cocotb, including edge cases (0, -1, smallest and largest values).
- Commit-log comparison against [Spike](https://github.com/riscv-software-src/riscv-isa-sim) (`--log-commits`), stopping at the first mismatch.
- Compliance: `riscv-tests`, later `riscv-arch-test` via RISCOF.
- Random program generator with saved seeds.
- Assertions, later SymbiYosys / riscv-formal.

Performance figures will be published only from measurements, with the date and commit hash.

## Getting started

Coming with stage 1. The planned entry points are `make lint`, `make unit`, `make sim PROG=...` and `make compare PROG=...`.

## Repository layout

```
rtl/      pkg, core, vec, gpu, soc
tb/       unit (cocotb), core (Verilator harness)
sw/       crt0, linker script, test programs, benchmarks
scripts/  Spike comparison, program generator, utilities
fpga/     constraints, build scripts
docs/     specs, ADRs, diagrams
```

Not all directories exist yet.

## License

CERN Open Hardware Licence Version 2, Strongly Reciprocal (CERN-OHL-S-2.0). See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Under this licence, modified designs and products made from them must be shared under the same terms. For other terms, contact the copyright holder.
