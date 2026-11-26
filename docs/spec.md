# Core specification

What the arp273 CPU core implements, as seen by software and by the system around it. This document
states the contract. The reasons and the measurements are in the [ADRs](adr/), the internals in the
[module descriptions](modules/overview.md).

There are two cores built from the same units:

| | `core_pipe` (the core: Sol 2, [ADR 015](adr/015-core-family.md)) | `core` (reference) |
|---|---|---|
| Purpose | The design that goes to the FPGA and later to silicon | A simple model to compare against in simulation |
| Structure | 5-stage pipeline ([ADR 013](adr/013-pipeline-and-memory-interface.md)) | Single cycle ([ADR 008](adr/008-single-cycle-memory-interface.md)) |
| Memory ports | valid/ready, any latency | Combinational read (not synthesizable to BRAM) |
| CSRs and exceptions | Yes ([ADR 014](adr/014-csr-and-exceptions.md)) | No: stops on any trap and on system instructions |

Everything below describes `core_pipe` unless stated otherwise.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `XLEN` (package `riscv_pkg`, macro `XLEN`) | 64 | 64: RV64IMA; 32: RV32IMA. Every width in the RTL follows it |
| `HART_ID` | 0 | Value of `mhartid` ([ADR 019](adr/019-soc-architecture.md): several harts on one memory) |
| `RESET_PC` | `0x8000_0000` | First instruction fetched after reset |
| `DEBUG` | 1 | 0: release build without the commit trace and the stall and flush counters; same behaviour on all functional outputs (`make release-check`) |
| `MUL_BITS` | 4 | Multiplier bits per cycle (1, 2, 4, 8; [ADR 017](adr/017-multiplier-bits-per-cycle.md)); 1 is Sol 1 |
| `BP` | 0 | Branch prediction in fetch ([ADR 018](adr/018-branch-prediction.md)): built and measured, not enabled by default. Sizes: `BHT_ENTRIES`, `BTB_ENTRIES`, `RAS_DEPTH`, `BTB_TAG_BITS`, `BTB_TARGET_BITS` ([modules/bpred.md](modules/bpred.md)) |

## Instruction set

ISA string: `rv64ima_zicsr_zifencei` (`rv32ima_zicsr_zifencei` for XLEN = 32), machine mode only.

| Group | Instructions |
|---|---|
| RV64I | All (RV32I for XLEN = 32): LUI, AUIPC, JAL, JALR, 6 branches, 7 loads (LD and LWU RV64 only), 4 stores (SD RV64 only), OP-IMM, OP, OP-IMM-32 and OP-32 (RV64 only), FENCE (a no-op: one hart, no caches) |
| M | All 13 (RV32: 8), iterative unit: see [Timing](#timing) |
| A | LR, SC and the 9 AMOs, `.W` and `.D` (RV32: `.W`), executed by the memory side of the data port ([ADR 019](adr/019-soc-architecture.md)); `aq` and `rl` are accepted and need nothing (one data request at a time, in order) |
| Zicsr | CSRRW, CSRRS, CSRRC, CSRRWI, CSRRSI, CSRRCI |
| Zifencei | FENCE.I: the instructions after it are fetched again |
| Privileged | ECALL, EBREAK, MRET; WFI is a no-op (no interrupts yet) |

Anything else is an illegal instruction: the C extension, F/D, URET, SRET, SFENCE.VMA, reserved
encodings, and system instructions with nonzero reserved fields.

Behaviour the specification leaves to the implementation, and the choice made here:

| Case | Behaviour |
|---|---|
| Misaligned load or store | Exception (cause 4 or 6), no hardware support |
| Misaligned LR, SC or AMO | Exception: LR cause 4, SC and AMOs cause 6 (as Spike) |
| LR/SC reservation | Kept by the memory side: one per hart, at the exact address of the LR; every SC clears it, a failed one too. An SC to another address fails |
| Jump or taken branch to an address with bit 1 set | Exception (cause 0) on the jump; no link register write |
| Division by zero, overflow (MIN / −1) | The results the M extension defines (−1 or the dividend; MIN and 0) |
| Register values after reset | Undefined: the register file has no reset ([ADR 005](adr/005-regfile-no-reset-no-x0-storage.md)); `sw/crt0.S` clears them |
| `mtval` | Written for every exception (see below) |

## CSRs and exceptions

The CSR set, their fields and the write rules are in [modules/csr.md](modules/csr.md). In short:
`mstatus` (MIE, MPIE; MPP reads M), `misa`, `mie`, `mtvec` (direct mode), `mscratch`, `mepc`, `mcause`,
`mtval`, `mip` (reads 0), `mcycle`, `minstret` (read-only in this core), `cycle`, `instret`, the ID registers
(read 0, except `mhartid` = `HART_ID`). `misa` shows I, M and A. Any other CSR address is an illegal instruction.

| Exception | `mcause` | `mtval` |
|---|---|---|
| Instruction address misaligned | 0 | jump target |
| Illegal instruction | 2 | instruction bits |
| Breakpoint | 3 | pc |
| Load address misaligned | 4 | address |
| Store/AMO address misaligned | 6 | address |
| Environment call from M-mode | 11 | 0 |

Exceptions are precise: every older instruction has completed, the trapping one and every younger one
have no effect (no register write, no store). On an exception: `mepc` = its pc, `mcause`, `mtval`,
`MPIE` = `MIE`, `MIE` = 0, execution continues at `mtvec`. MRET: `MIE` = `MPIE`, `MPIE` = 1, execution
continues at `mepc`.

Interrupts are not implemented yet (`mie` and `MIE` are writable but nothing raises an interrupt).

## Memory map

The core has no address decoder of its own: every address goes out on its ports. The system around it
(the simulation harness today, the SoC in stage 2) decides what is where.

| Address | Today (simulation) | Plan (SoC, stage 2) |
|---|---|---|
| `0x0000_0000` | — | Boot ROM |
| `0x0200_0000` | — | Timer (`mtime`, `mtimecmp`) |
| `0x1000_0000` | — | UART |
| `0x1000_1000` | — | GPIO |
| `0x2000_0000` | — | GPU registers |
| `0x8000_0000` | RAM, 1 MiB (program, data, stack) | RAM, shared by the CPU and the GPU |

Programs end by writing `(exit code << 1) | 1` to the symbol `tohost` (as on Spike). The same ELF runs on
Spike and on both cores (`sw/link.ld`, `sw/crt0.S`).

## Ports

Two memory ports, one for instructions and one for data, with the same protocol
([ADR 013](adr/013-pipeline-and-memory-interface.md)):

- **Request:** `req_valid`, `req_ready`, `req_addr`. The data port adds `req_we`, `req_wstrb` (`XLEN/8` byte
  strobes) and `req_wdata`, with the data already in its byte lane. It transfers in a cycle where valid and
  ready are both 1. A request that has not been accepted may change or be withdrawn: a memory must not
  assume it is held.
- **Response:** `rsp_valid`, `rsp_data`. Exactly one response per accepted request (stores too), at least
  one cycle after it, in order. There is no `rsp_ready`: the core always takes it.
- **Outstanding requests:** at most one per port. A new request may be accepted in the cycle the previous
  response arrives.
- **Data addresses** are aligned to `XLEN/8` bytes; the core selects and extends the bytes itself.
  **Instruction addresses** are 4-byte aligned; an instruction is 32 bits.
- **Atomics** (A extension, [ADR 019](adr/019-soc-architecture.md)): the data port also has `req_amo` and
  `req_atop` (5 bits: `funct5` of the instruction, `riscv_pkg::amo_op_t`). An atomic is one request with
  `req_amo` = 1: `req_we` = 1 except for LR, `req_wstrb` gives the lanes of the access (LR included),
  `req_wdata` the operand in its lane. The memory side executes it and responds with the old word (LR,
  AMOs) or, for SC, 0 (stored) or 1 (failed) in the lane. Today the simulation models do this; in the
  SoC it is the L2.

A synchronous BRAM (always ready, response in the next cycle) gives one access per cycle on each port.

Other outputs: `trap_o` (an exception is taken in this cycle); the commit trace `commit_*` (debug build
only, used to compare with Spike); the counters `perf_cycles_o`, `perf_instret_o`, `perf_stalls_o`,
`perf_flushes_o` (64 bits; stalls and flushes only in the debug build). Full port list:
[modules/core_pipe.md](modules/core_pipe.md).

## Timing

Cycles on a BRAM (always ready, one-cycle response):

| Event | Cost |
|---|---|
| Any instruction without a hazard | 1 cycle (one per cycle in steady state) |
| Result used by the next instruction (ALU, jump link, M result) | 0: forwarded |
| Load or CSR read used by the next instruction | +1 cycle |
| Taken branch, JAL, JALR | +2 cycles (decided in EX; with the optional predictor, `BP = 1`: 0 when predicted right, +2 when wrong) |
| Not-taken branch | 0 |
| Multiplication (holds EX) | `N / MUL_BITS + 2` cycles, `N = XLEN/2` when the multiplier fits in `XLEN/2` bits ([ADR 011](adr/011-muldiv-short-path.md)), otherwise `N = XLEN`; with `MUL_BITS = 4`: 10 or 18 cycles for XLEN = 64 |
| Division (holds EX) | `XLEN/2 + 2` cycles when the dividend fits in `XLEN/2` bits, otherwise `XLEN + 2` |
| Exception, MRET, FENCE.I | Younger instructions dropped; fetch restarts in the next cycle |
| Pipeline fill after reset | 4 cycles |

Measured CPI and area are in [ADR 013](adr/013-pipeline-and-memory-interface.md),
[ADR 014](adr/014-csr-and-exceptions.md), [ADR 017](adr/017-multiplier-bits-per-cycle.md), [ADR 018](adr/018-branch-prediction.md)
and the [README](../README.md). No clock frequency is claimed
until place-and-route on the FPGA.

## Not implemented

Planned ([ADR 019](adr/019-soc-architecture.md)): interrupts and the timer (CLINT, PLIC), the A, C,
Zba, Zbb, Zicbom and Zicboz extensions, user mode and PMP, write-back L1 caches with a coherent L2
(MSI), the SoC bus, the GPU, the out-of-order cores Spica and Naos (F and D for Naos are to be
evaluated). In the reserve: the vector unit, supervisor mode and virtual memory (Linux), a debug
module. Not planned: misaligned access in hardware.
