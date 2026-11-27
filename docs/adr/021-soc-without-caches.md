# ADR 021: The first SoC: several harts on one memory, an atomics unit, startup of several harts

## Context

[ADR 019](019-soc-architecture.md) plans the SoC with write-back L1 caches and a coherent L2 that also
executes the atomics. Building all of that at once would mix three kinds of bugs: those of several harts
sharing memory, those of the atomics, and those of the cache protocol. Step 3 of stage 2 therefore puts
two (or more) Sol cores on one memory without caches first. When the caches come, this SoC is the reference
for them: the same programs must give the same results.

The cores already send atomics as single requests with their operation (A extension, ADR 019), and until
now the simulation models executed them. In the SoC that has to be RTL.

## Options

| Question | Options | Chosen |
|---|---|---|
| Interconnect | a crossbar to banked memory; **one shared instruction port and one shared data port, round-robin, one request in flight** | the shared ports: the simplest thing that is correct; bandwidth comes back with the L1s |
| Where the atomics run | in each core (lock the bus); **in one unit in front of memory** | the unit (`amo_unit`): the same place and logic as in the L2 later |
| LR/SC reservation | one global; **one per hart, at the LR's byte address; another hart's write to the same aligned word clears it; the hart's own store does not** | per hart: what the specification asks of several harts; the own-store rule is Spike's |
| Starting several harts | a boot ROM that parks harts; **`crt0` reads `mhartid`: hart 0 runs `main`, the others call `secondary(hartid)` on their own stacks** | `crt0` with `-DSMP` only: the single-cycle reference core stops on CSR instructions, and the existing programs keep their exact instruction streams |

## Decision

- **`rr_arbiter`**: N masters, one port, one request in flight, a new grant in the cycle the previous
  response arrives, the response routed back by the saved owner, round-robin priority (a requesting master
  waits at most N − 1 grants).
- **`amo_unit`**: loads and stores pass through. LR is a load that sets the hart's reservation. SC with a
  matching reservation is a store that responds 0; otherwise it sends nothing and responds 1. Either way the
  hart's reservation is cleared. An AMO is a read and then a write of the result, with the response the old
  word; it cannot be split, because the arbiter grants nothing else while it runs.
- **`soc_top`**: `NHARTS` cores with `HART_ID` 0 … N − 1, an instruction arbiter, a data arbiter, then
  the address: `0x0200_0000`–`0x0200_FFFF` goes to the CLINT (`rtl/soc/clint.sv`, one per SoC, lines to
  every hart), the rest goes through `amo_unit` to the outside. `irq_meip_i` comes from outside until the
  PLIC.
- **Software**: `sw/crt0.S` with `-DSMP` gives hart h a 32 KiB stack below hart 0's and calls the weak
  `secondary(h)` (by default it returns and the hart sleeps in WFI). Programs for several harts are in
  `sw/smp`, built with `-DSMP`, and run by `make smp` on the SoC and on `spike -p2`.

## Consequences

- **Performance is not the point of this step.** Two cores share one memory port; on `sw/smp/counter.c`
  each core has CPI 2.31 (2026-11-27, BRAM model, not committed RTL), against about 1.1–1.2 for one core
  on plain code. The L1 caches of step 4 remove the sharing.
- **Verification.**
  - `test_rr_arbiter.py` and `test_amo_unit.py` drive each unit with random traffic against a reference.
    The atomics unit gets three harts and four words, so reservations collide often.
  - `test_soc.py` runs two harts on the RTL: a shared AMO counter, a reservation broken by the other hart's
    store, wake-up from WFI by `msip` and by the hart's own timer. It uses a BRAM and two stalling memories.
  - `make smp` runs C programs: lost updates (AMO, LR/SC compare-and-swap, spinlock), interrupts between
    harts, and a race-free program compared with Spike hart by hart. Each runs on the SoC and on Spike.
  - The commit-log comparison now checks the value an AMO writes (`compare.py` used to read only the load
    part of its log line), and takes `--hart`.
- **Atomics to the CLINT** act as plain accesses; atomics are meant for RAM.
- **Stays for later.** Each arbiter has one request in flight; the L2 of step 4 will accept a request per
  core. The reservation granule is one aligned word; with caches it becomes the cache line, which the
  specification allows.
