# SoC top (`rtl/soc/soc_top.sv`) and its units

The SoC of stage 2, step 3 ([ADR 021](../adr/021-soc-without-caches.md)): `NHARTS` Sol cores on one memory,
without caches. Plan of the whole SoC: [ADR 019](../adr/019-soc-architecture.md).

```
 core 0 ─┐ fetch                         ┌─ mem_i_* (instruction memory)
 core 1 ─┴─ rr_arbiter ──────────────────┘
 core 0 ─┐ data            ┌─ 0x0200_xxxx ─ clint ─── msip, mtip of every hart
 core 1 ─┴─ rr_arbiter ────┤
                           └─ otherwise ── amo_unit ─ mem_d_* (RAM, UART, tohost)
```

## `soc_top`

| Port | Direction | Width | Description |
|---|---|---|---|
| `clk_i`, `rst_ni`, `tick_i` | input | 1 | Clock, reset, `mtime` increment |
| `mem_i_req_*`, `mem_i_rsp_*` | | | The shared instruction port (the core protocol, 32-bit responses) |
| `mem_d_req_*`, `mem_d_rsp_*` | | | The shared data port: plain loads and stores (no `amo`) |
| `irq_meip_i` | input | `NHARTS` | External interrupt of each hart (no PLIC yet) |
| `trap_o`, `commit_*`, `perf_cycles_o`, `perf_instret_o` | output | per hart | The outputs of the cores side by side: hart h in bits `[h*W +: W]` |

| Parameter | Default | Meaning |
|---|---|---|
| `NHARTS` | 2 | Cores; `HART_ID` = 0 … `NHARTS` − 1 |
| `RESET_PC`, `DEBUG`, `MUL_BITS`, `BP` | as `core_pipe` | Passed to every core |

## `rr_arbiter`

N masters share one port. One request is in flight at a time; a new one is granted in the cycle the
previous response arrives. The response goes to the master that sent the request, and `req_id_o` names it
downstream. Round-robin: after a grant, the next master has the highest priority, so a master that keeps
requesting waits at most N − 1 grants. Parameters: `N`, `W` (request bits), `RW` (response bits).

## `amo_unit`

Executes the A extension in front of memory. The memory below sees only loads and stores.

| Request | What happens | Response |
|---|---|---|
| load, store | passed through | memory's |
| LR | a load; the hart's reservation = its byte address | the word |
| SC, reservation matches | a store; the hart's reservation is cleared | 0 in the lane |
| SC, no match | nothing goes to memory; the hart's reservation is cleared | 1 in the lane |
| AMO | a read, then the write of the result (indivisible: the arbiter grants nothing else meanwhile) | the old word |

A store, a successful SC or an AMO clears the reservations of the other harts on the same aligned word.
A hart's own store leaves its reservation (as Spike does). `.W` operations work on a 4-byte lane (the
strobes say which), `.D` on the whole word. Parameter: `NHARTS`.

## Testing

- `tb/unit/test_rr_arbiter.py`: three masters with random traffic on a stalling, late-answering port.
  It checks one request in flight, payload and id, the routing of responses, at most N − 1 grants passing
  over a waiting master, a grant in the response cycle, and the exact rotation 0, 1, 2 under full load.
- `tb/unit/test_amo_unit.py`: 3000 random requests from three harts to four words against a reference
  model. It checks every response, the final memory, one memory access in flight, a failed SC not reaching
  memory, and an AMO as exactly a read then a write.
- `tb/unit/test_soc.py`: two harts on the RTL with a BRAM and two stalling memories:
  - `mhartid`;
  - a shared AMO counter;
  - an LR reservation broken by the other hart's store;
  - wake-up from WFI by `msip` and by the hart's own `mtimecmp`.
- `make smp`: `sw/smp/*.c` on the SoC (`tb/soc/main.cpp`) and on `spike -p2`:
  - `counter.c`: AMO, compare-and-swap, spinlock;
  - `ipi.c`: software and timer interrupts between harts;
  - `race_free.c`: compared with Spike for each hart.
- Mutation check (2026-11-27): `rr_arbiter` 10 mutants, 9 caught, 1 equivalent (a response with nothing in
  flight, which the protocol excludes); `amo_unit` 21, all caught; `soc_top` 7, all caught.
