# ADR 014: Machine-mode CSRs and precise exceptions in the pipeline

## Context

Until now every trap stopped the pipelined core (ADR 013): an illegal instruction, a misaligned access or
ECALL froze it in WB. That is enough for programs that end through tohost, but not for:
- `riscv-tests` (`rv64ui-p`, `rv64um-p`): their startup code writes CSRs, enters the test with MRET, and
  reports the result from a trap handler entered by ECALL;
- a real system: an operating system, a driver or a firmware handles exceptions and interrupts.

The plan for stage 1 asks for the minimal M-mode CSRs and ECALL as a real exception. The single-cycle
core stays the reference for user-level code (ADR 008) and does not get them.

## Options

**Which CSRs**

| Option | Pros | Cons |
|---|---|---|
| A. Only `mstatus`, `mtvec`, `mepc`, `mcause` | Smallest | `riscv-tests` read `mtval`, `misa`, `mhartid`; counters are not readable; `mscratch` is the usual way into a handler |
| **B. The M-mode set a single-hart M-only core needs:** `mstatus`, `misa`, `mie`, `mtvec`, `mscratch`, `mepc`, `mcause`, `mtval`, `mip`, the counters (`mcycle`, `minstret`, `cycle`, `instret`) and the ID registers | Runs `riscv-tests`, a handler can save registers through `mscratch`, programs can measure themselves | ~750 LUT6 of read mux and registers at XLEN = 64 |
| C. B plus S and U modes, delegation, PMP, `satp` | Needed for an operating system with virtual memory | Several times the logic and the tests; nothing in the plan needs it this year |

**Where to take an exception**

| Option | Pros | Cons |
|---|---|---|
| In EX, as soon as it is known | Shorter flush | Not precise: an older load or store still in MEM or WB may trap after it; a misaligned access is only known in MEM |
| **In WB, in program order** | Precise by construction: every older instruction has committed, no younger one has written a register or sent a store | A few cycles later than possible; exceptions are rare |

**Where to access CSRs**

| Option | Pros | Cons |
|---|---|---|
| In EX, with forwarding of CSR values | The value is ready early | CSR writes in EX are not precise (an older instruction may still trap); a CSR read must see the writes of older CSR instructions in MEM and WB |
| **In WB, by the committing instruction** | In order with everything else; no CSR hazards between CSR instructions, a trap and MRET | A CSR read is known only in WB: the next instruction that needs it waits like after a load |

## Decision

Option B, with exceptions and CSR access in WB (`rtl/common/csr.sv`, description in
[modules/csr.md](../modules/csr.md)).

**Decoder.** It recognizes:
- the six CSR instructions;
- the fixed encodings of ECALL, EBREAK, MRET and WFI (WFI is a no-op: there are no interrupts yet);
- FENCE.I.

Other SYSTEM encodings (URET, SRET, SFENCE.VMA, ECALL with nonzero fields...) stay illegal. Whether a CSR
exists, and whether it may be written, is decided by the CSR unit, because it knows the address map.

**Flow of an exception:**
- **ID/EX.** An illegal instruction or a CSR instruction carries its own instruction bits instead of the
  immediate. They give `mtval` of an illegal instruction, and the CSR address and 5-bit immediate.
- **EX.** It finds ECALL (cause 11), EBREAK (3), illegal (2) and the misaligned jump (0, ADR 013). The value
  passed on is `mtval`: the instruction bits, the jump target, the pc of EBREAK, or 0.
- **MEM.** It adds a misaligned load (4) or store (6), with the address as `mtval`.
- **WB.** It adds an illegal CSR access (2, with the instruction rebuilt from the fields WB carries).
- **Taking it.** WB takes the exception without committing:
  - `mepc`, `mcause` and `mtval` are written;
  - `MPIE` gets `MIE` and `MIE` is cleared;
  - every younger instruction is dropped (EX, MEM, the fetch queue, a response on the way);
  - the multiplier/divider is aborted (`abort_i`, new);
  - fetch restarts at `mtvec`.

**MRET and FENCE.I commit in WB and redirect:**
- MRET goes to `mepc` and restores `MIE` from `MPIE`;
- FENCE.I goes to `pc + 4`. The instructions after it are fetched again, after every older store.

The fetch for a WB redirect starts one cycle later than for a branch in EX. Fetching in the same cycle
would put the WB decision, a mux and an adder in series in front of the fetch address. Exceptions are
rare, so this costs nothing measurable. The data request of the instruction in MEM is held back while WB
redirects. Everything else needs no extra term: the flush branch of the pipeline registers wins over the
stage handshakes. The mutation check showed that a flush term in the EX and MEM handshakes changed nothing,
and it was removed.

**CSR instructions in the pipeline:**
- the write value (rs1, or the zero-extended immediate) travels in the result field;
- WB reads the old value into rd and writes the new one in the same cycle;
- a CSR read is not forwarded from MEM: the next instruction waits one cycle in EX, the same mechanism as
  load-use;
- CSRRS and CSRRC with rs1 = x0 (or a zero immediate) do not write, so a read-only CSR can be read with
  them;
- a write to a read-only CSR (address bits 11:10 = 11) or to a CSR that does not exist is an illegal
  instruction.

**Counters:**
- `mcycle` and `minstret` read the performance counters of ADR 013;
- `minstret` counts the instructions retired before the reading one, as in Spike;
- writes to them are ignored. This is a simplification: the specification makes them writable. It is
  recorded here and in [modules/csr.md](../modules/csr.md).

**Not supported:**
- misaligned loads and stores. They trap, which the specification allows, so `rv64ui-p-ma_data` is not
  run. A handler could emulate them later;
- interrupts. `mie` and `mstatus.MIE` exist and are writable, nothing raises them yet; the timer is a later
  step (SoC).

**The single-cycle reference core** stops on CSR instructions, ECALL, EBREAK, MRET and FENCE.I as on an
illegal instruction. It has no CSRs.

**Harness and tests:**
- **Harness.** `tb/core/main.cpp` still stops at the first exception by default. The C programs in `sw/`
  install no handler, so stopping there gives a clear failure instead of a jump to `mtvec = 0`. With
  `TRAP_STOP=0` the core enters its handler.
- **Unit tests.** In `tb/unit/test_core.py`, `run_pipe` can take a given number of exceptions
  (`skip_traps`) before it stops.

## Measurements

Measured 2026-11-25, no commit hash (estimate), Yosys 0.66, `make area`:

| | before (ADR 013) | with CSRs and exceptions |
|---|---|---|
| `core_pipe` LUT6 / flip-flops, XLEN = 64 | 5129 / 3621 | 5920 / 4000 |
| `core_pipe` LUT6 / flip-flops, XLEN = 32 | 2706 / 2078 | 3400 / 2296 |
| release build (`DEBUG = 0`), LUT6 / flip-flops, XLEN = 64 | 4962 / 3232 | 5907 / 3741 |
| `csr` alone, LUT6 / flip-flops, XLEN = 64 | — | 746 / 323 |
| xc7 logic delay, debug / release, XLEN = 64 | 7067 / 5445 ps | 7303 / 5484 ps |

Notes on the numbers:
- The CSR unit is about half of the growth. Its 64-bit registers (`mtvec`, `mscratch`, `mepc`, `mcause`,
  `mtval`) and the read mux over 20 addresses make most of it.
- The rest is the exception state carried through EX, MEM and WB: the cause, 17 bits of CSR address and
  rs1 field, the system flags, and the wider result mux in EX.
- The delay numbers come from different ABC runs: the differences are within the noise seen before (ADR 013).

CPI on the BRAM is unchanged (`make sim CORE=pipe`): `fib` 1.141, `alu` 1.103, `mem` 1.216, `mbench` 6.167,
`muldiv` 17.256. The programs take no exceptions, and the branch path is untouched.

## Verification

- **`tb/unit/test_csr.py`** (8 tests, XLEN = 64 and 32). Each runs on a BRAM and on three slow memories,
  and the commit traces must be identical. They cover:
  - every exception cause, with `mepc` and `mtval`, no effect of the trapping instruction, and resumption
    after it;
  - CSRRW/S/C and the immediate forms, a CSR read used by the next instruction at once, WARL and read-only
    CSRs;
  - `mstatus` through a trap and MRET;
  - MRET without a trap;
  - no store after MRET;
  - FENCE.I executing an overwritten instruction;
  - an exception that aborts a division in progress;
  - the counters.
- **`test_decoder.py`:** the new instructions and 8 more illegal SYSTEM and MISC-MEM encodings.
- **`test_muldiv.py`:** abort in the middle of an operation.
- **Mutation check:**

  | Module | Mutants | Caught | Equivalent |
  |---|---|---|---|
  | `core_pipe` | 52 | 51 | 1 (the one of ADR 013) |
  | `csr` | 17 | 16 | 1 |

  The equivalent `csr` mutant is "write without `write_i`": set or clear with 0 writes back the old value.
  Two mutants first survived:
  - a store right behind MRET was not held back. It led to a new test;
  - the flush term in the EX handshake. It was redundant and was removed.
- **`make riscv-tests`:** `rv64ui-p` and `rv64um-p`, 66 of 66 pass, also with `MEM_STALL=0.3`; `ma_data` is
  not run (see above). A broken XOR (as OR) in the ALU fails `rv64ui-p-xor` and `xori`: the tests check
  results, not only that they finish.
- **Unchanged:**
  - `make regress` and `make fuzz` (both cores) match Spike;
  - `make release-check` passes;
  - `make lint` and `make struct` are clean.

## Consequences

- Stage 1 of the plan is functionally complete. The open items are measurements on the board (Fmax) and
  the author's review.
- **`riscv-tests` becomes a git submodule.** The author adds it (`third_party/riscv-tests`, with its env
  submodule); CI checks it out recursively.
- **Next in the plan (stage 2):** the timer and interrupts (`mtime`, `mtimecmp`, `mip.MTIP`), then the
  SoC bus. Interrupts are taken at the same point as exceptions, in WB.
- **Later optimizations, only after measurements:**
  - a smaller CSR read mux (fewer 64-bit CSRs, or read-only zeros where allowed);
  - fetching on a WB redirect in the same cycle.
