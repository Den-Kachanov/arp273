# Pipelined core (`rtl/sol/core_pipe.sv`)

A five-stage pipelined RV64IMA core (RV32IMA for XLEN = 32): IF, ID, EX, MEM, WB. Design decisions,
measurements and verification: [ADR 013](../adr/013-pipeline-and-memory-interface.md); Sol 2 adds the
multiplier width ([ADR 017](../adr/017-multiplier-bits-per-cycle.md)) and branch prediction (ADR 018). It
is built from the same units as the single-cycle reference core ([core.md](core.md)). In the family of
cores (ADR 015) it is **Sol**: with the defaults Sol 2, with `MUL_BITS = 1` Sol 1 cycle for cycle. Branch
prediction (`BP = 1`) was built and measured but is not enabled: it did not lower energy × time (ADR 018).

![Five-stage pipeline: stages, forwarding, redirects and memory ports](../img/pipeline.svg)

## Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `clk_i`, `rst_ni` | input | 1 | Clock, synchronous reset (active low) |
| `imem_req_valid_o`, `imem_req_ready_i`, `imem_req_addr_o` | out, in, out | 1, 1, `XLEN` | Instruction request |
| `imem_rsp_valid_i`, `imem_rsp_data_i` | input | 1, 32 | Instruction response |
| `dmem_req_valid_o`, `dmem_req_ready_i` | out, in | 1 | Data request handshake |
| `dmem_req_addr_o`, `dmem_req_we_o`, `dmem_req_wstrb_o`, `dmem_req_wdata_o` | output | `XLEN`, 1, `XLEN/8`, `XLEN` | Aligned address, write enable, byte strobes, data in its lane |
| `dmem_req_amo_o`, `dmem_req_atop_o` | output | 1, 5 | An atomic of the A extension and its operation (`funct5`); the memory side executes it ([ADR 019](../adr/019-soc-architecture.md), [spec](../spec.md#ports)) |
| `dmem_rsp_valid_i`, `dmem_rsp_data_i` | input | 1, `XLEN` | Data response (a store gets one too; an atomic gets the old word or the SC result) |
| `irq_msip_i`, `irq_mtip_i`, `irq_meip_i` | input | 1 | Interrupt lines (levels) from the CLINT and the PLIC ([ADR 020](../adr/020-interrupts.md)) |
| `trap_o` | output | 1 | An exception or interrupt is taken in this cycle; `commit_pc_o` shows the instruction it replaces (ADR 014) |
| `commit_valid_o`, `commit_pc_o`, `commit_inst_o`, `commit_we_o`, `commit_rd_o`, `commit_data_o` | output | | Commit trace from WB, as in the single-cycle core |
| `commit_mem_re_o`, `commit_mem_we_o`, `commit_mem_addr_o`, `commit_mem_wdata_o`, `commit_mem_wstrb_o` | output | | The memory access of the committed instruction, for the commit log |
| `perf_cycles_o`, `perf_instret_o`, `perf_stalls_o`, `perf_flushes_o` | output | 64 | Cycles, retired instructions, cycles with a valid instruction held in ID, redirects from EX and WB (taken transfers in Sol 1, mispredictions with prediction) |

| Parameter | Default | Meaning |
|---|---|---|
| `RESET_PC` | `0x8000_0000` | First fetch |
| `HART_ID` | 0 | Value of `mhartid` |
| `DEBUG` | 1 | 0: release build, no commit trace (the `commit_*` outputs read 0) and no stall and flush counters; the behaviour on the memory ports is the same (`make release-check`, ADR 013) |
| `MUL_BITS` | 4 | Multiplier bits per cycle of `muldiv` (1, 2, 4, 8; ADR 017) |
| `BP` | 0 | Branch prediction in IF ([bpred.md](bpred.md), ADR 018); 0 removes the predictor. Not enabled by default: measured, it costs as much time as it saves cycles and more energy |
| `BHT_ENTRIES`, `BTB_ENTRIES`, `RAS_DEPTH`, `BTB_TAG_BITS`, `BTB_TARGET_BITS` | 64, 16, 8, 10, 20 | Sizes of the predictor |

## Memory protocol (ADR 013)

- A request transfers when `req_valid` and `req_ready` are 1 in the same cycle. A request that was not
  accepted may change or be withdrawn.
- One response per accepted request, at least one cycle later, in order; the core always takes it.
- At most one outstanding request per port; a new one may be accepted in the cycle the previous
  response arrives. A synchronous BRAM (always ready, answer in the next cycle) gives one access per cycle.

## Stages

| Stage | Does | Holds when |
|---|---|---|
| IF | issues fetch requests; responses go into a 2-entry queue; with `BP`, looks up the predictor at `pc_f_q` and fetches the predicted target next | the queue would overflow, or a response is outstanding |
| ID | the queue head: decode, register read (write-through from WB) | EX cannot take it |
| EX | forwarding, ALU, branch decision and jump targets, multiplier/divider; ECALL, EBREAK, illegal and misaligned-jump exceptions | an operand is a load or CSR read still in MEM, or a load in WB without its response; the multiplier/divider is busy; MEM cannot take it |
| MEM | sends the data request (aligned, strobes from `lsu`) | WB cannot take it, or the request is not accepted |
| WB | waits for the response of a load or store, aligns the load, reads and writes CSRs, writes the register, commits; takes exceptions, MRET and FENCE.I | the response has not arrived |

- **Forwarding** into EX from MEM (ALU, link or M results, not loads) and from WB (including load data),
  never from an instruction that writes x0; MEM has priority (the younger value).
- **Load-use:** one cycle, the consumer waits in EX.
- **Taken branches and jumps** are decided in EX: the ID instruction and the fetch queue are flushed, a
  response on the way is dropped, and the target is fetched in the same cycle when the port allows:
  two cycles per taken branch.
- **With prediction** (`BP = 1`): the guess for each fetch (taken, and the low `BTB_TARGET_BITS` of the
  target) travels with the instruction to EX. EX redirects only when the guess was wrong: the direction,
  the target (all its bits are compared), or a "taken" guess on an instruction that is no transfer at
  all (a stale entry after FENCE.I, or an aliased tag); a wrongly predicted fall-through continues at
  `pc + 4`. A misprediction costs the same two cycles as a taken branch in Sol 1; a correct prediction
  costs nothing. The fetch at an EX redirect target is not predicted when it goes out in the redirect cycle,
  which keeps the lookup off the redirect path (if the port is not ready then, the target is fetched
  from `pc_f_q` a cycle later and is predicted, which is harmless). EX updates the predictor with every control instruction that leaves it and is not
  dropped (also a jump that traps on its misaligned target: harmless, as the BTB drops target bits 1:0
  and the trap drops the predicted path).
- **Exceptions** (illegal instruction, ECALL, EBREAK, misaligned load, store or jump target, illegal CSR
  access) travel to WB and are taken there in program order: `mepc`, `mcause`, `mtval` are written, every
  younger instruction is dropped, fetch restarts at `mtvec` one cycle later. MRET (to `mepc`) and FENCE.I
  (to `pc + 4`) commit and redirect the same way. CSRs: [csr.md](csr.md), [ADR 014](../adr/014-csr-and-exceptions.md).

## Testing

Every test of the core runs in three configurations (`CONFIGS` in `tb/unit/test_core.py`): `sol1`
(`MUL_BITS = 1, BP = 0`), `sol2` (the defaults) and `sol2-bp` (`BP = 1`), for XLEN = 64 and 32.

- `tb/unit/test_core.py`: the 14 core tests also run on this core (`test_core_pipe_runner`), against a memory
  that stalls at random.
- `tb/unit/test_pipeline.py`: 15 hazard tests, each on a BRAM with exact cycle counts and on three slow
  memories with identical commit traces. With prediction: a loop (2 flushes instead of 9), calls and
  returns from one site (4 instead of 8), a JALR whose target moves by exactly 2^20 (the BTB keeps only
  the low 20 bits, so EX must compare the upper bits too).
- `tb/unit/test_atomic.py`: 6 tests of the A extension and `mhartid` in every configuration and on a core with `HART_ID = 5`: every `.W` and `.D` operation, a `.W` atomic in the upper half of a doubleword, LR/SC (success, failure without a reservation, at another address, twice), the result used by the next instruction, the exceptions of misaligned atomics.
- `tb/unit/test_irq.py`: 7 tests of interrupts with a model of the CLINT and an external line, every configuration: software, timer and external interrupts in a loop with loads, stores, an AMO, a multiplication and a division, priority, masking and `mip`, WFI with MIE clear and set, stores and AMOs in WB when the interrupt rises (none may run twice). On a BRAM and four stalling memories the main program must commit exactly what it commits with MIE off, and every `mepc` must be the first instruction after MRET.
- `tb/unit/test_csr.py`: 12 tests of CSRs and exceptions (ADR 014), among them a branch overwritten by
  FENCE.I while the BTB still predicts it, an exception on the predicted path, a misaligned jump that
  traps every time (also once learned), and a branch dropped by FENCE.I that must not train the predictor.
- `make mutate M=core_pipe`: 85 mutants (prediction, A extension and interrupts included), 84 caught, 1 equivalent; every
  mutant runs in the three configurations.
- `make riscv-tests`: `rv64ui-p`, `rv64um-p`, `rv64ua-p` and `rv64mi-p`, 99 of 99 run (4 not run: `ma_data`, misaligned access traps; `breakpoint`, `pmpaddr`, `instret_overflow`: debug triggers, PMP, writable `mcycle`/`minstret` are not built yet), also with `MEM_STALL=0.3`.
- `make irq`: `sw/irq/timer.c` (timer and software interrupts, WFI) returns 0 on the core and on Spike.
- `make regress CORE=pipe` (with `sw/a/atomic.c`: fetch-and-op builtins, compare-and-swap as an LR/SC loop, a spinlock), `make fuzz CORE=pipe`, also with `MEM_STALL=0.3`: match Spike.

## Atomics (A extension)

An LR, SC or AMO goes through the pipeline like a load: its address is rs1 (EX passes rs1 instead of the
ALU result), its data is rs2 in its lane (the MEM `lsu` with `funct3` = 2 or 3 gives the lanes of a
word or a doubleword), and its result arrives in WB as load data, sign-extended by the WB `lsu`. The
operation `inst[31:27]` travels in the bits that carry the CSR address (`ex_imm_q`, `mem_sys_q`), so
it costs no new pipeline register; only the `amo` flag is new in EX and MEM. The memory side executes
the operation and keeps the LR reservation, as the L2 will (ADR 019). Because the core has one data
request in flight and completes every access in order, `aq` and `rl` need nothing. A misaligned LR
raises cause 4, a misaligned SC or AMO cause 6.

Cost, measured on 2026-11-27 (Yosys 0.66, release build, Sol 2 parameters, XLEN = 64, against the RTL of
commit `0603b07`; the change itself is not committed yet; ABC moves such numbers by a few percent between
runs): `core_pipe` 6516 → 6616 LUT6 (+1.5 %), 3740 → 3742 flip-flops; on xc7 4836 → 4750 LUT (within the
noise) and logic delay 6098 → 6168 ps (+1.1 %); the decoder 68 → 78 LUT6. Timing of everything else
is unchanged: the exact cycle counts of `test_pipeline.py` pass as before.

## Interrupts and WFI

[ADR 020](../adr/020-interrupts.md). The lines are registered in `csr`. An interrupt that is pending,
enabled in `mie` and allowed by `mstatus.MIE` is taken in WB in place of the instruction there, through
the exception path (`mepc` = its pc, `mtval` = 0), unless that instruction is a load, store or atomic
(its request has already gone to memory: it commits, the next instruction takes the interrupt) or a WFI.
WFI waits in WB until an interrupt is pending and enabled in `mie`, then commits; with MIE set the next
instruction takes the interrupt (`mepc` = WFI + 4). The `wfi` flag travels with the instruction like
`mret`; nothing else in the earlier stages knows about interrupts.

## Watching it run

`make pipeview PROG=<program>` (`scripts/pipeview.py`, page `tools/pipeview/pipeview.html`) runs a program
on a build with waves and writes `build/pipeview/<program>.html`: one self-contained page that steps
through the run cycle by cycle. It shows each stage's instruction and why it waits, the fetch queue,
forwarding into EX, branch and trap redirects, the requests on both memory ports, the register file,
the CSRs and interrupt lines, a pipeline chart and the program listing. `PIPEVIEW_CYCLES` limits the
length (default 4000); programs from `sw/irq` run with their handlers. The page needs no server: open
the file in a browser (arrow keys step, Shift+arrow by 10, Space plays, `#cycle-N` opens a cycle).

## Status

`make lint` and `make struct` are clean for XLEN = 64 and 32. Area and logic delay are in
[ADR 013](../adr/013-pipeline-and-memory-interface.md) and [overview.md](overview.md); Fmax needs
place-and-route on the board.
