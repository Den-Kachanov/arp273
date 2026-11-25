# ADR 005: Register file without reset and without storage for x0

## Context

The project rules require the same synchronous reset `rst_ni` in every module. A 32 × `XLEN` register file maps well onto distributed RAM (LUT-RAM) on an FPGA if it has no reset. By the specification, register x0 is always 0.

## Options

| Option | Pros | Cons |
|---|---|---|
| **A. No reset; array `mem[1:31]`, a read of address 0 returns 0** | The array can become RAM instead of 31 × `XLEN` flip-flops; x0 takes no storage | A register that was never written holds an undefined value; an exception to the `rst_ni` rule |
| B. Reset to 0 | Same behaviour as the other modules; everything deterministic | The reset prevents mapping the array to RAM; more area |

The area claim is reasoning; it can be checked by synthesis once the regfile exists.

## Decision

Option A. By convention a program must not read a register before writing it; crt0 initialises the registers it needs.

## Consequences

- The tests never read a register that has not been written.
- The exception to the `rst_ni` rule must be documented in `riscv_pkg` or the README.
- Comparison with Spike: Spike resets the registers to 0, our core does not. This only diverges for programs that read a register before writing it, and such programs are considered wrong. Update in ADR 012: `crt0` now clears x1–x31, because compiled code legitimately saves callee-saved registers it never wrote.
- Later measurement (2026-11-25, `make area --xc7`): on Xilinx 7 the regfile does map to distributed RAM (22 × RAM32M, 88 LUTs as RAM plus 141 LUTs, XLEN = 64), while generic synthesis keeps flip-flops (1644 LUT6).
