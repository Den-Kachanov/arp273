# ADR 020: Interrupts in WB, WFI, and the CLINT

## Context

The SoC of [ADR 019](019-soc-architecture.md) needs the machine timer, software interrupts between
harts (IPIs) and external interrupts. Sol takes exceptions in WB, in order, by freezing the pipeline
and flushing everything younger ([ADR 014](014-csr-and-exceptions.md)). An interrupt arrives
asynchronously and must be precise too: one instruction is the last to complete, the next one has not
started, and `mepc` is that next one.

Two facts of the pipeline shape the choice:
- A load, store or atomic sends its request to memory in MEM, so by the time it is in WB the access has
  happened (a store is written, an AMO executed). It cannot be undone and replayed.
- Every other instruction changes nothing outside the core before WB: register writes, CSR writes and
  MRET all happen in WB.

## Options

| Option | Pros | Cons |
|---|---|---|
| **A. Take the interrupt in WB, in place of the WB instruction, except a memory access** | Reuses the exception path (mepc = pc of the WB instruction, flush, fetch mtvec); nothing new in the earlier stages | A memory access in WB delays the interrupt by one instruction |
| B. Take it in ID or EX, marking the instruction as trapping | Earlier | The instruction travels through MEM: a load or store would have to be suppressed there; more interactions with stalls and forwarding |
| C. Inject it after the WB instruction commits, with mepc = the next pc | Never delays | The next pc must be known in WB (a taken branch, a flush in flight): more logic in the most critical stage |

For WFI:

| Option | Pros | Cons |
|---|---|---|
| **W1. WFI waits in WB until an interrupt is pending and enabled in `mie`, then commits; the interrupt is taken on the next instruction** (mepc = WFI + 4) | `while (!flag) wfi();` re-checks the flag after the handler; this is what Spike does when the interrupt arrives during the wait | — |
| W2. The interrupt replaces the waiting WFI (mepc = WFI) | Uniform with other instructions | After MRET the WFI sleeps again: the loop above hangs until the next interrupt |
| W3. WFI is a no-op | Simplest; allowed by the specification | No sleep, no energy saved: against the project's goal |

## Decision

Options A and W1.

- **Lines.** `irq_msip_i`, `irq_mtip_i`, `irq_meip_i`: levels, registered once in `csr` (`mip` shows
  them, writes to `mip` are ignored).
- **Taking an interrupt.** It is taken when `mstatus.MIE` is set and a line is pending with its `mie`
  bit set. Priority: MEI (11) > MSI (3) > MTI (7), as the specification orders them. It is taken in WB,
  on a valid instruction that is not a load, store, atomic or WFI. That instruction does not execute:
  `mepc` = its pc, `mcause` = interrupt bit + code, `mtval` = 0, and the exception path does the rest
  (MIE, MPIE, flush, fetch from `mtvec`).
- **WFI.** WFI waits in WB while nothing is pending and enabled (whatever MIE is), then commits.
- **CLINT** (`rtl/soc/clint.sv`): `msip`, `mtimecmp` and `mtime` at the SiFive offsets that Spike and
  QEMU use. `mtime` counts on `tick_i` (every cycle in simulation; a fixed-rate tick on the board).
  `mtimecmp` resets to all ones. The register port is the core's data-port protocol; AXI-Lite comes with
  the SoC bus.

## Consequences

- **Latency** from a line rising to the first handler instruction in WB, on a BRAM: two cycles to
  register the line (CLINT output, then `csr`), the WB instruction's own cycle, then the refill from
  `mtvec`. At most one extra instruction when a memory access is in WB.
- **Verification.**
  - `tb/unit/test_irq.py` checks precision, not traces: on a BRAM and on four stalling memories, the main
    program must commit exactly what it commits with MIE off, and every handler entry must show the
    expected `mcause`, `mtval` = 0, and a `mepc` that is the first instruction after MRET. Its loop
    contains an AMO, so an access replayed after an interrupt would change the result.
  - `tb/unit/test_clint.py` tests the CLINT alone.
  - `sw/irq/timer.c` (`make irq`) is a C program with a timer and a software interrupt. It runs on the
    core and on Spike, and both must return 0.
  - `rv64mi-p` runs: 14 of 17 tests pass. The other three need debug triggers, PMP (stage 2b), or
    writable `mcycle`/`minstret`. The last is a gap against the specification left from ADR 014 and must
    be fixed before RISCOF.
- **Not comparable with Spike instruction by instruction**: Spike's `mtime` runs at a different rate, so
  where an interrupt lands differs. Programs with interrupts check themselves.
- **No interrupt controller for external sources yet**: `irq_meip_i` comes straight from the harness;
  the PLIC comes with the SoC bus.
