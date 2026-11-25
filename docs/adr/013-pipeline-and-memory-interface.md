# ADR 013: Five-stage pipeline and a valid/ready memory interface

Status: draft, pending author review

## Context

Stage 1 of the plan replaces the single-cycle core with a classic five-stage pipeline (IF, ID, EX, MEM,
WB). The single-cycle core reads both memories combinationally (ADR 008), which no BRAM and no bus can
do. The project rules require a valid/ready handshake on memory ports and synchronous reads, and every
stage able to stall. The single-cycle core stays in the repository as the reference (`core.sv`); the
pipeline is a new top module, `core_pipe.sv`, built from the same units (`decoder`, `regfile`,
`imm_gen`, `alu`, `branch_unit`, `lsu`, `muldiv`, `perf_counters`).

## Options

**Memory interface**

| Option | Pros | Cons |
|---|---|---|
| A. Combinational read (ADR 008) | Simplest core | No BRAM, no bus, no wait states |
| B. Fixed one-cycle synchronous read, no handshake | Simple, matches a BRAM | No wait states: caches, buses, slow memories impossible |
| **C. valid/ready request, response one or more cycles later, in order, one outstanding request per port** | Works with a BRAM (always ready, one cycle) and with anything slower; one outstanding request keeps the core simple | One request per port in flight limits a slow memory to one access per round trip |
| D. AXI or OBI directly | Standard buses | Far more signals and rules than the core needs now; an adapter can provide them later |

Within C, a request can be "sticky" (once valid, it must stay with the same contents until ready, as in
AXI and OBI) or not (the core may change or drop a request that was not accepted). A redirect wants to
change the fetch address immediately.

**Fetch**: replay the request while ID stalls (works only with a memory that answers in a fixed time),
or a small queue that takes the responses whatever the timing.

**Load-use and branches**: where to detect the load-use hazard (ID or EX) and where to decide branches
(EX, the classic choice, or ID, with a shorter penalty and a longer path).

## Decision

**Interface C, not sticky** (one port for instructions, one for data):
- `req_valid`, `req_ready`, address (and `we`, `wstrb`, `wdata` on the data port). A request transfers
  in a cycle where valid and ready are both 1. A request that has not been accepted may change or be
  withdrawn in the next cycle; the memory side must not rely on it being held. An adapter to AXI or
  OBI would add the holding register.
- `rsp_valid`, `rsp_data`: exactly one response per accepted request (stores too), at least one cycle
  after the transfer, in order. The core always takes a response; there is no `rsp_ready`.
- At most one outstanding request per port; a new one may be accepted in the cycle the previous
  response arrives. A synchronous BRAM is always ready and answers in the next cycle: one access per
  cycle.

**Pipeline** (`rtl/core/core_pipe.sv`, description in [modules/core_pipe.md](../modules/core_pipe.md)):
- **IF:** requests go out from a fetch address register; responses enter a two-entry fetch queue whose
  head is the ID instruction. A request is only issued when the queue will have room for its response.
  On a taken branch or jump the target is fetched in the same cycle when the port allows it, so the
  penalty is two cycles; `target + 4` comes from a second adder in EX (base + (imm + 4), with imm + 4
  computed in ID), so that the redirect decision is not followed by an adder in the same cycle.
  A response still on the way when the path changes is dropped.
- **ID:** decode and register read; the register file has `WRITE_THROUGH = 1` (ADR 004), so a register
  written in WB is read in the same cycle.
- **EX:** forwarding from MEM (not loads) and from WB, never for x0; ALU, branch decision, jump
  targets, misaligned target trap. Load-use: the consumer waits in EX while the load is in MEM, one
  cycle (a check in ID was first written as well; the mutation check showed it changed nothing, it was
  removed). While EX waits, its operand registers follow the newest forwarded values, because the
  producer may leave WB meanwhile. The multiplier/divider holds EX until done; its result is kept if
  MEM cannot take it in the done cycle.
- **MEM:** the data request goes out only when WB can take the instruction in the same cycle, so the
  response meets it in WB; a misaligned or trapping access never goes out.
- **WB:** loads and stores wait for their response; the load is aligned and extended here; register
  write and commit. A trap reaches WB and freezes the pipeline: younger instructions write no register
  and send no store.
- The commit trace now also carries the memory access of the committed instruction
  (`commit_mem_*`), because the store happens in MEM, before the commit.

## Measurements

Measured 2026-11-25, no commit hash (estimate). Yosys 0.66; programs on the Verilator harness
(`make sim CORE=pipe`), the memory a BRAM unless stated.

**CPI** (performance counters):

| Program | single-cycle | pipeline, BRAM | pipeline, `MEM_STALL=0.3` |
|---|---|---|---|
| `fib` | 1.000 | 1.141 | 3.243 |
| `alu` | 1.000 | 1.103 | 3.118 |
| `mem` | 1.000 | 1.216 | 3.335 |
| `mbench` | 6.004 | 6.167 | 7.635 |
| `muldiv` | 17.102 | 17.256 | 18.553 |

On the BRAM the extra cycles are what the design predicts: 4 cycles to fill the pipeline, 2 per taken
branch (`fib`: 20 taken branches, 348 cycles for 305 instructions), 1 per load-use. The multiplier
waits are the same as in the single-cycle core: EX holds them. The pipeline does not lower the CPI; it
is meant to raise the clock frequency.

**Area and logic delay** (`make area`):

| | single-cycle `core` | `core_pipe` |
|---|---|---|
| LUT6 / flip-flops, XLEN = 64 | 4592 / 2515 | 5129 / 3621 |
| LUT6 / flip-flops, XLEN = 32 | 2312 / 1393 | 2706 / 2078 |
| xc7 LUT / CARRY4, XLEN = 64 | 2360 / 176 | 3227 / 216 |
| xc7 logic delay, XLEN = 64 | 6934 ps | 7067 ps |

The logic delay comes from `sta` with cell delays only, no routing and **no memory access time**. For
the single-cycle core this hides two memory reads in series in its one cycle (instruction, then data);
the pipeline reads memory only at stage boundaries. The two numbers therefore do not compare the
clock periods, and no Fmax is claimed here: that needs place-and-route on the board.

The first version had a longer path, 7225 ps: forwarding → branch compare → choice of the fetch
address → a 64-bit `+4` adder → fetch address register (two carry chains in series, introduced by
fetching the target in the redirect cycle). Computing `target + 4` in parallel removed it. The longest
path is now the EX stage fed by forwarding from WB: load alignment of the response → forwarding mux →
EX logic (here the operand negation of the multiplier/divider).

## Verification

- **Unit tests:** the 14 core tests of `test_core.py` run on both cores; on the pipeline against a
  memory that stalls at random (ready 70 %, responses 1 to 4 cycles late). `test_pipeline.py` adds 12
  hazard tests. Each runs on a BRAM with exact cycle counts (one instruction per cycle, one cycle per
  load-use, two per taken branch, no stall for forwarding) and then on three slow memories (both ports
  slow, and a fast instruction port with a slow data port); the commit traces must be identical.
- **Protocol checks in the memory models:** at most one outstanding request per port, aligned
  data requests, no misaligned instruction fetch.
- **Mutation check:** `make mutate M=core_pipe`, 31 mutants (forwarding, x0, priority, operand refresh,
  load waits, flush, drop of a stale response, queue, multiplier/divider, memory port, traps, counters):
  30 caught, 1 equivalent (without the result hold the multiplier/divider computes the result again:
  only time is lost). Four mutants first survived and showed missing tests (two writes to one register,
  a producer leaving WB while EX waits, a load feeding the multiplier, a trapping jump that fetched its
  target) and one redundant piece of logic (the load-use check in ID).
- **Spike:** `make regress CORE=pipe` (5 programs) and `make fuzz CORE=pipe` (40 seeds) match, also with
  `MEM_STALL=0.3` and `0.5`.
- `make lint` and `make struct` (both cores, XLEN = 64 and 32) are clean.

## Consequences

- ADR 008 now only describes the single-cycle reference core.
- A taken branch costs 2 cycles. Deciding branches in ID or predicting them is a later speed feature,
  enabled only after measurements on CoreMark.
- The non-sticky request is simpler for the core; a bridge to AXI or OBI must hold the request.
- One outstanding request per port halves the throughput of a memory with two cycles of latency; more
  outstanding requests need a deeper fetch queue and tagging, which a cache will need anyway.
- Next steps of stage 1: CSRs (`mstatus`, `mtvec`, `mepc`, `mcause`) and ECALL as a real exception
  (today every trap stops the core), then `riscv-tests` (`rv64ui-p`, `rv64um-p`).
