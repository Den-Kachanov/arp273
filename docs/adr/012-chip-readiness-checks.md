# ADR 012: Chip-readiness checks: random initial state, structural rules, clean registers in crt0

## Context

The author wants the RTL to move to silicon later without rework. Several things that the simulator hides break on a chip. They were found while working on ADR 011.

1. **Zeros at start-up.** By default Verilator sets every register to 0. A flip-flop without reset on a chip (and on an FPGA without initialisation) starts with any value. The `muldiv` mutant "`last_q` is not cleared on load" survived precisely because Verilator zeroed `last_q`.
2. **A different start in Spike.** Spike starts with all registers at zero, while the register file without reset (ADR 005) holds garbage. Once the initial values became random, `mbench` diverged from Spike at instruction 4: `main` saves to the stack a callee-saved register that nobody had written. This is not a core bug but a difference between the models.
3. **Latches, asynchronous resets, combinational loops and multiple drivers** are often let through by FPGA synthesis. A chip flow dislikes or forbids them.

## Decision

1. **Random initial register values in every simulation.**
   - Unit tests (`tb/unit/common.py`): `--x-assign unique --x-initial unique` and `+verilator+rand+reset+2`.
   - Core harness (`tb/core/main.cpp`, `Makefile`): the same flags, `Verilated::randReset(2)`.
   - The seed is fixed (`X_SEED`, default 1), so a failure repeats; another seed is set through the environment variable.
2. **`sw/crt0.S` clears x1–x31 at start-up.** `riscv-tests` and ordinary bare-metal code do the same. A program then behaves identically on Spike, in simulation and on the chip.
3. **`make struct` (also in CI)**: Yosys after `proc` and `flatten`, before any optimisation.
   - `check -assert`: combinational loops, multiple drivers, undriven signals.
   - Forbidden: latches (`$dlatch`, `$adlatch`), asynchronous resets and set/reset (`$adff`, `$adffe`, `$aldff`, `$sr`, `$dffsr`, `$dffsre`). The project uses the synchronous `rst_ni`.
   - Checked that the target really catches problems: on a copy with an added latch and an added loop `make struct` fails; on the current core it passes for XLEN = 64 and 32. The first version ran `opt_clean` before the checks and caught nothing, because the unused latch and loop were removed before they were checked.

## Verification

- `make unit` (18 pytest tests) passes with random initial values;
- the mutation checks of all 9 modules were rerun on the new infrastructure (no survivors; the equivalent ones are unchanged: regfile 1, decoder 1);
- `make regress` and `make fuzz` (40) match Spike;
- `mbench` and `muldiv` additionally with `X_SEED` = 2, 3, 7.

## Consequences

- **Programs:** every program is now 31 instructions longer (register clearing in `crt0`). Cycle counts measured before this change cannot be compared with new ones directly. For ADR 011, "before" and "after" were measured with the same `crt0`.
- **Seed coverage:** one seed does not cover every initial state. For registers that correctness depends on, a dedicated test that writes garbage into them is better (like `test_garbage_after_power_up`).
- **Not covered yet:** `make struct` does not replace the checks of a chip flow (DFT/scan chains, clock domains, timing after routing). That is stage 5a (OpenLane).
