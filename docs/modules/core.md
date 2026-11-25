# Single-cycle core (`rtl/core/core.sv`)

Status: draft, pending author review

A single-cycle RV64IM core (RV32IM for XLEN = 32): one instruction per cycle, except M instructions, which wait for `muldiv` (XLEN/2 + 1 or XLEN + 1 cycles depending on the operands, ADR 011). It consists of `decoder`, `regfile` (`WRITE_THROUGH = 0`), `imm_gen`, `alu`, `branch_unit`, `lsu`, `muldiv`, `perf_counters`, the PC register, the `a_sel`, `b_sel`, `wb_sel` muxes and the next-PC select. Connections: [overview.md](overview.md); memory interface: [ADR 008](../adr/008-single-cycle-memory-interface.md).

## Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `clk_i`, `rst_ni` | input | 1 | Clock, synchronous reset (active low) |
| `imem_addr_o` | output | `XLEN` | Instruction address (PC) |
| `imem_rdata_i` | input | 32 | Instruction, combinational read |
| `dmem_addr_o`, `dmem_re_o`, `dmem_wdata_o`, `dmem_wstrb_o` | output | `XLEN`, 1, `XLEN`, `XLEN/8` | Data access (the address is the ALU result) |
| `dmem_rdata_i` | input | `XLEN` | Data word, combinational read |
| `trap_o` | output | 1 | Illegal instruction, misaligned load/store, or a jump (JAL, JALR, taken branch) to an address with bit 1 set (no C extension); the PC does not move, nothing is written, `rd` is not written |
| `commit_valid_o`, `commit_pc_o`, `commit_inst_o`, `commit_we_o`, `commit_rd_o`, `commit_data_o` | output | | Commit log: writes to x0 are not reported. `commit_valid_o = 0` during a trap and while an M instruction waits; an M instruction is committed once, in the `done` cycle |
| `perf_cycles_o`, `perf_instret_o`, `perf_stalls_o`, `perf_flushes_o` | output | 64 | Counters: cycles since reset, retired instructions, stalls (cycles waiting for `muldiv`), flushes (JAL, JALR, taken branch) |

Parameter `RESET_PC` (default `0x8000_0000`).

## Next PC

`pc + 4`; for JAL and a taken branch it is `alu_r` (= `pc + imm`); for JALR it is `alu_r` (= `rs1 + imm`) with bit 0 cleared; on a trap and while an M instruction waits the PC holds.

## Waiting for an M instruction

`stall = muldiv & ~md_done`. While `stall = 1`: the PC does not move, no register is written (`reg_we_eff` is off), `commit_valid_o = 0`, and the `stalls` counter increments. In the `done` cycle (`md_done = 1`) the result goes to rd through `wb_sel = WB_MD`, the instruction commits and the PC moves on. The operands (rs1, rs2) stay stable during the whole wait, because the PC and the registers do not change; `muldiv` also copies them in the first cycle.

## Testing

`tb/unit/test_core.py`, `make unit T=core`, XLEN = 64 and 32. Programs are assembled by the mini-assembler `tb/unit/asm.py`; the memory is modelled in Python. Fifteen tests: sum 1..10, ALU and LUI/AUIPC, W instructions (RV64), loads/stores of every size, JAL/JALR and branches, counters, x0, illegal instruction, misaligned load and store, misaligned jump target (and a branch that is not taken does not trap), all M instructions, a loop with `MUL` (factorial, division and remainder).

The M tests check: the result values; that every instruction commits exactly once (consecutive pcs, no repeats); `rd = x0` and the same register as source and destination; `stalls` equals the sum of `iterations + 1` for every M instruction according to the latency model `mext.iterations` (ADR 011); `cycles = instret + stalls`. `run_program` also checks that there is no register write and no store during a wait.

Mutation check: `make mutate M=core`, 23 mutants (next PC, traps, misaligned targets, commit log, M integration), all caught.

## Simulation and comparison with Spike

- `sw/crt0.S`, `sw/link.ld`: start at `0x8000_0000`, the stack at the end of 1 MiB of RAM, the program reports its exit code by writing `(code << 1) | 1` to `tohost`. `crt0` clears x1–x31 first, because the register file has no reset (ADR 012). The same ELF runs on Spike and on the core.
- Programs:
  - `sw/fib.c`: Fibonacci numbers;
  - `sw/mem.c`: loads and stores of every size and signedness;
  - `sw/alu.c`: arithmetic with shifts and comparisons;
  - `sw/muldiv.c`: all 13 M instructions on edge and random operands (inline assembly forces the exact instruction);
  - `sw/mbench.c`: M instructions on ordinary numbers.

  Built with `-march=rv64im_zicsr -mabi=lp64 -mcmodel=medany`; `libgcc` is not needed, because the hardware multiplies and divides.
- `tb/core/main.cpp`: the Verilator harness (XLEN = 64 only). It loads the binary, answers memory requests in the same cycle, writes a commit log in the style of `spike --log-commits` (cycles waiting for an M instruction are skipped: `commit_valid_o = 0`) and stops on a write to `tohost` or on a trap. It prints the counters and the CPI. Registers start with random values (ADR 012).
- `scripts/compare.py`: normalises both logs to `(pc, instruction, register write, store)`, skips Spike's boot ROM (pc < `0x8000_0000`), stops at the first difference and shows the five preceding lines. The core log must match the beginning of the Spike log.
- `scripts/gen_prog.py`: random RV64IM programs from a seed (`make fuzz`).
- Commands: `make sim PROG=fib`, `make compare PROG=fib`, `make regress` (all programs in `sw/`), `make fuzz SEEDS="1 2 3"`.

Result (measured 2026-11-25, no commit hash): `make regress` (Spike with `--isa=rv64im`) matches for `alu` (551 instructions), `fib` (305), `mbench` (3591), `mem` (481), `muldiv` (11545); `make fuzz` matches for 40 seeds. `muldiv` runs in 197441 cycles (CPI 17.102), `mbench` in 21560 cycles (CPI 6.004); programs without M have CPI 1.000. `muldiv.c` is a deliberate worst case, not an estimate for real programs.

Sensitivity check: broken RTL copies (write strobe not shifted, halfword sign taken from the wrong bit, BNE as BEQ) are caught by the comparison. The mutant "SRAW sign from bit 63" is not caught by the programs (the compiler keeps 32-bit values sign-extended, so bits 31 and 63 are equal); the ALU tests catch it. The mutant "JALR does not clear bit 0" is not caught either, because return addresses are even; `test_core.py` catches it.

## Status

`make lint` and `make struct` are clean, `make unit` passes (18 runs: 9 modules × XLEN 64 and 32), Yosys synthesizes, `make regress` and `make fuzz` are green, CI is configured (`.github/workflows/ci.yml`). Without M instructions the CPI of the single-cycle core is 1 by construction (a check of the counters, not a performance measurement).

Area of the whole core (measured 2026-11-25, Yosys 0.66, `make area`, no commit hash, estimate): XLEN = 64: 4546 LUT6 + 2515 flip-flops, on xc7 2371 LUTs + 88 LUTs as RAM; XLEN = 32: 2317 LUT6 + 1393 flip-flops.

Not done yet: `riscv-tests` (`rv64ui-p`, `rv64um-p`), CSRs and ECALL (currently a trap), counters readable as CSRs, the pipeline.
