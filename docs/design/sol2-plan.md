# Sol 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Sol 2 from Sol 1 (`core_pipe`): an energy measurement, a multiplier with 4 or 8 bits per cycle, and branch prediction in fetch, each accepted only if it lowers the energy-delay product per CoreMark iteration.

**Architecture:** Everything new is a parameter of the existing RTL. The parameter values that reproduce Sol 1 exactly (`MUL_BITS = 1`, `BP = 0`) stay available, so the exact-cycle tests of Sol 1 keep running and the `s` variant (ADR 015) needs no other design. Measurement first (task 1), then each change is built, measured on `make bench` and `make energy`, and recorded in an ADR.

**Tech Stack:** SystemVerilog (Yosys 0.66 subset, ADR 003), Verilator 5, cocotb, Python 3.12, Yosys `abc -g cmos2`, riscv64-elf-gcc 16.2.

**Spec:** [docs/design/sol2.md](sol2.md)

## Global Constraints

- Project conventions and ADR 003: `always_ff` only `<=`, `always_comb` defaults for every signal, every `case` has `default`, synchronous active-low reset `rst_ni`, `_i`/`_o`/`_q`/`_d` names, no fixed widths (`[XLEN-1:0]`, never `[63:0]`), RV64-only logic under `if (XLEN == 64)`.
- Both XLEN = 64 and 32 must build, lint (`make lint`) and pass `make unit`; `make struct` clean (no latches, async resets, loops, multiple drivers).
- Acceptance of a change: the energy-delay product per CoreMark iteration (energy per iteration × cycles per iteration) goes down. Area is reported for every change.
- Numbers in documents only from measurements, with the date and the RTL commit hash; model estimates are labelled as estimates.
- Everything in the repository in English; no `Date:` and no `Status` lines in documents; an ADR changed by a later one gets an `Update:` line.
- New source files start with the two SPDX lines (`make spdx`).
- The author runs git. Every "commit" step means: give the author the exact `git add` / `git commit` commands.
- After each task: `make lint struct unit`, `make regress CORE=pipe`, `make riscv-tests`, `make release-check` and `make bench SOFTFP=0` stay green.

## Review Focus

1. **A stale BTB entry after FENCE.I or self-modifying code** predicts "taken" at a pc whose instruction is no longer a branch: EX must treat a predicted-taken non-control instruction as a misprediction and continue at pc + 4. Test in task 5.
2. **A predicted fetch and a WB redirect in the same cycle** (exception or MRET while the predictor redirects fetch): the WB redirect must win, and the drop of stale responses must still work on a stalling memory. Test in task 5.
3. **A jump to a misaligned target** (cause 0) must not be written into the BTB, and a misprediction on an instruction that later traps must not leave the core fetching the wrong path. Test in task 5.
4. **Multi-bit multiplication with a signed multiplier** (MULH, MULHSU; the top bit has a negative weight and now falls inside a group of 4 or 8 bits), on the short and the full path, for XLEN = 32 too. Test in task 2.
5. **An abort in the middle of a multi-bit multiplication** returns the unit to idle, and the next operation is correct. Test in task 2.

---

### Task 1: Energy measurement (`make energy`)

**Files:**
- Create: `scripts/energy.py`
- Create: `docs/adr/016-energy-estimate.md`
- Modify: `Makefile` (target `energy`, help line), `.github/workflows/ci.yml` (step `make bench SOFTFP=0` in the job that runs riscv-tests), `docs/design/sol2.md` (Sol 1 energy numbers)

**Interfaces:**
- Produces: `scripts/energy.py --top core_pipe [--param NAME=VALUE ...] --bench <name> [--bench ...] --json <path>`; JSON `{"meta": {...}, "results": {<bench>: {"cycles", "toggle_load", "clock", "energy", "transistors"}, "coremark_iteration": {"cycles", "energy", "edp"}}}`. `make energy [PARAMS="MUL_BITS=4 BP=1"]` writes `build/energy/results.json`. Later tasks call it with `--param`.

- [ ] **Step 1: Netlist.** In `energy.py`, synthesize the release build (`hierarchy -top core_pipe -chparam DEBUG 0` plus each `--param`), `synth -flatten`, `abc -g cmos2`, `opt_clean`, then `write_verilog -noattr` (gates as assign expressions) and `write_json` (for fanout), and `stat -tech cmos` for the transistor count.
- [ ] **Step 2: Simulation.** Build the netlist with the existing harness (`tb/core/main.cpp`, `-DPIPE -DRELEASE`, `--prefix Vtop`, `--x-initial unique`, same `X_SEED` default) plus `--coverage-toggle`; run each benchmark binary from `build/bench/` (built by `scripts/bench.py`); read the toggle counts from `coverage.dat`.
- [ ] **Step 3: Energy model.** `energy = Σ over net bits (transitions × (fanout + 1)) + CLOCK_WEIGHT × flip-flops × cycles`, with `CLOCK_WEIGHT = 2` (two clock edges, load 1) and fanout = number of cell inputs the net bit drives (from the JSON). Relative units, stated as such.
- [ ] **Step 4: Check the counting.** The bit 0 of the cycle counter in `perf_counters` must show `transitions == cycles ± 2`, and bit 1 `cycles / 2 ± 2`; the script fails otherwise.
- [ ] **Step 5: Per CoreMark iteration.** Run CoreMark built with `ITERATIONS=1` and `ITERATIONS=3`; per iteration = (run 3 − run 1) / 2 for cycles and energy, so start-up and printing cancel; `edp = energy × cycles` per iteration.
- [ ] **Step 6: Run the Sol 1 baseline.** `make energy BENCH="coremark eb-edn eb-nettle-aes"`. Expected: the check of step 4 passes; numbers printed for the three benchmarks and per CoreMark iteration; save as `docs/results/sol1-energy.json`.
- [ ] **Step 7: ADR 016.** The model, its assumptions (no wire capacitance, all gates equal per input, no clock gating, no leakage), the check of step 4, the Sol 1 numbers with date and commit `3c7be96`, and what the model cannot show.
- [ ] **Step 8: CI and gates.** Add `make bench SOFTFP=0` to CI after riscv-tests; run the gates of Global Constraints.
- [ ] **Step 9: Commit (author).** `scripts/energy.py Makefile .github/workflows/ci.yml docs/adr/016-energy-estimate.md docs/results/sol1-energy.json docs/design/sol2.md`.

### Task 2: `muldiv` with `MUL_BITS` bits per cycle

**Files:**
- Modify: `rtl/common/muldiv.sv`, `tb/unit/mext.py`, `tb/unit/test_muldiv.py`, `scripts/mutants/muldiv.py`, `docs/modules/muldiv.md`

**Interfaces:**
- Produces: `module muldiv #(parameter int MUL_BITS = 1)`, ports unchanged. `MUL_BITS` ∈ {1, 2, 4, 8}, must divide `XLEN / 2`. Multiplication iterations: `N / MUL_BITS` with `N = XLEN / 2` (short path, ADR 011) or `XLEN`; division iterations unchanged (`N` at one bit per cycle). Latency stays iterations + 1 edges.
- Produces: `mext.iterations(funct3, a, b, word=0, mul_bits=1) -> int`.

- [ ] **Step 1: Model.** `mext.iterations` divides the multiplication iterations by `mul_bits` (funct3 < 4); division unchanged.
- [ ] **Step 2: Tests per width.** `test_muldiv_runner` parametrized over `xlen` × `mul_bits` ∈ {1, 4, 8}, passing `build_args=[f"-GMUL_BITS={mul_bits}"]` to `common.run` and `MUL_BITS` in the environment; every test uses `mext.iterations(..., mul_bits=int(os.environ["MUL_BITS"]))`. Add `test_signed_multiplier_groups`: MULH and MULHSU with b ∈ {MIN, −1, MIN + 1, −2^(XLEN/2), 2^(XLEN/2) − 1} and a ∈ edge values, results equal to `mext.expected` and latency equal to `mext.iterations` (Review Focus 4). Extend `test_abort` to abort after 1, 2 and `iterations − 1` cycles of a MULH (Review Focus 5).
- [ ] **Step 3: Run, expect failures** for `mul_bits` 4 and 8 (latency): `make unit T=muldiv`.
- [ ] **Step 4: RTL.** In each multiply iteration add the `MUL_BITS` partial products `b_j · A · 2^j` (A the sign-extended multiplicand, `XLEN + 1` bits) to `hi` through a carry-save tree and one carry-propagate adder of `XLEN + MUL_BITS + 1` bits, then shift `{hi, lo}` right by `MUL_BITS`. The last group of a signed multiplier subtracts its top partial product. The divider keeps the existing adder; the multiplier gets its own path when `MUL_BITS > 1` (the shared adder is kept for `MUL_BITS = 1`, so Sol 1 is unchanged gate for gate). `cnt_q` counts groups.
- [ ] **Step 5: Run** `make unit T=muldiv` for XLEN 64 and 32: all pass. `make lint struct`.
- [ ] **Step 6: Mutants.** Add mutants for the group sum (a partial product dropped, the negative weight on the wrong row, the shift by `MUL_BITS − 1`) to `scripts/mutants/muldiv.py`; `make mutate M=muldiv`: no survivor (equivalent ones explained).
- [ ] **Step 7: Docs.** `docs/modules/muldiv.md`: parameter, latency table per `MUL_BITS`.
- [ ] **Step 8: Commit (author).**

### Task 3: Choose the multiplier (measure 4 and 8)

**Files:**
- Modify: `rtl/sol/core_pipe.sv` (parameter `MUL_BITS` passed to `u_muldiv`), `rtl/ref/core.sv` (`MUL_BITS = 1` fixed), `Makefile` (`PARAMS` passed as `-G` to the simulation builds, and to `area.py`/`energy.py`), `scripts/area.py` (`--param`)
- Create: `docs/adr/017-multiplier-bits-per-cycle.md`

**Interfaces:**
- Consumes: `muldiv #(MUL_BITS)` (task 2), `energy.py --param` (task 1).
- Produces: `core_pipe #(parameter int MUL_BITS = <chosen>)`.

- [ ] **Step 1:** Parameter in `core_pipe`, default 1 for now; `make regress CORE=pipe PARAMS="MUL_BITS=4"` and `=8`, `make riscv-tests` with both, `MEM_STALL=0.3 make fuzz CORE=pipe` with both: all match Spike.
- [ ] **Step 2: Measure** for `MUL_BITS` = 1, 4, 8: `make bench` (all 29), `make energy BENCH="coremark eb-edn eb-nettle-aes"`, `make area` (generic LUT6, xc7, transistors). Expected (model, not result): CoreMark/MHz about 1.95 and 2.11.
- [ ] **Step 3: Decide** by the energy-delay product per CoreMark iteration; set the default; record the other value as a parameter option.
- [ ] **Step 4: ADR 017** with the three columns (cycles, CoreMark/MHz, geometric mean over 29, energy, EDP, area, xc7 delay of `muldiv`), date and commit.
- [ ] **Step 5:** Gates; save `docs/results/sol2-mul.json`; commit (author).

### Task 4: Branch predictor unit (`bpred`)

**Files:**
- Create: `rtl/sol/bpred.sv`, `tb/unit/test_bpred.py`, `scripts/mutants/bpred.py`, `docs/modules/bpred.md`
- Modify: `tb/unit/test_core.py` (`MODULES` gets `bpred`), `scripts/area.py` (module list)

**Interfaces:**
- Produces:

```systemverilog
module bpred #(
	parameter int BHT_ENTRIES = 64,   // power of two
	parameter int BTB_ENTRIES = 16,   // power of two
	parameter int RAS_DEPTH   = 8
) (
	input  logic clk_i, rst_ni,
	// lookup: combinational from the fetch address
	input  logic [XLEN-1:0] pc_i,
	output logic            taken_o,      // predicted taken (BTB hit and: jump, call, return, or branch with counter >= 2)
	output logic [XLEN-1:0] target_o,     // BTB target, or the top of the return stack for a return
	input  logic            accept_i,     // the fetch at pc_i was accepted: a call pushes pc_i + 4, a return pops
	// update: one control instruction resolved in EX
	input  logic            upd_i,
	input  logic [XLEN-1:0] upd_pc_i,
	input  logic [1:0]      upd_kind_i,   // 0 branch, 1 jump, 2 call (rd = x1/x5), 3 return (JALR rs1 = x1/x5, rd = x0)
	input  logic            upd_taken_i,
	input  logic [XLEN-1:0] upd_target_i
);
```

  BTB: direct-mapped by `pc[2 +: log2(BTB_ENTRIES)]`, entry = valid, tag (the remaining pc bits above bit 1), target, kind. Only `valid` is reset. A taken update writes the entry; a not-taken branch updates only the BHT. BHT: 2-bit counters by `pc[2 +: log2(BHT_ENTRIES)]`, no reset; a BTB write of a branch sets its counter to 2 (weakly taken), so a counter is never read before it is written. Return stack: circular, `RAS_DEPTH` entries, overflow overwrites the oldest, no repair after a misprediction (performance only).

- [ ] **Step 1: Tests** in `test_bpred.py` (XLEN 64 and 32): `test_reset_misses` (any pc: `taken_o == 0`); `test_branch_learns` (taken update at P → lookup P taken with target T; two not-taken updates → not taken; one taken → taken); `test_alias` (P and P + 4·BTB_ENTRIES: the second replaces the first, the first misses); `test_jump_always_taken`; `test_call_return` (call at P accepted → return lookup gives P + 4); `test_ras_overflow` (`RAS_DEPTH + 1` nested calls, then returns: the first `RAS_DEPTH` targets right, the last one is what overwrote it); `test_lookup_without_accept_keeps_ras`.
- [ ] **Step 2: Run, expect failure** (no module): `make unit T=bpred`.
- [ ] **Step 3: RTL** `rtl/sol/bpred.sv` per the interface above.
- [ ] **Step 4: Run** `make unit T=bpred`, `make lint struct`: pass.
- [ ] **Step 5: Mutants** (wrong index bits, tag not compared, counter saturation off, push/pop swapped, valid not reset): `make mutate M=bpred`, no survivor.
- [ ] **Step 6: Docs** `docs/modules/bpred.md` (ports, entry format, sizes) and a diagram `docs/img/bpred.svg`; commit (author).

### Task 5: Prediction in the pipeline

**Files:**
- Modify: `rtl/sol/core_pipe.sv`, `tb/unit/test_pipeline.py`, `tb/unit/test_csr.py`, `tb/unit/test_core.py`, `scripts/mutants/core_pipe.py`, `docs/modules/core_pipe.md`, `docs/img/pipeline.svg`

**Interfaces:**
- Consumes: `bpred` (task 4).
- Produces: `core_pipe #(parameter bit BP = 1, parameter int BHT_ENTRIES = 64, BTB_ENTRIES = 16, RAS_DEPTH = 8)`; with `BP = 0` the core is Sol 1 cycle for cycle.

- [ ] **Step 1: Tests first.** In `test_pipeline.py`: the existing exact-cycle tests build with `BP=0 MUL_BITS=1` (they describe Sol 1); a second runner builds `BP=1` and runs every functional test (`both()` against the reference core on BRAM and the three stalling memories). New tests on BRAM, written as the difference in cycles between a `BP=0` and a `BP=1` build of the same program: `test_loop_predicted` (a 10-iteration backward loop: 14 cycles fewer), `test_call_return_predicted` (a function called 3 times; each JAL is preceded by an ADDI and the function has an ADDI before its RET, so no call or return is the first fetch after a redirect: 8 cycles fewer). Both numbers follow from the rules: Sol 1 pays 2 cycles per taken transfer; with `BP = 1` only the first sight of each transfer (BTB empty) and the loop exit (counter says taken) pay. In `test_csr.py`: `test_stale_btb_after_fence_i` (a taken branch runs twice, is overwritten with ADDI, FENCE.I, runs again: the result of the ADDI path, Review Focus 1); `test_trap_on_predicted_path` (ECALL right after a predicted-taken branch and an MRET back: same trace as the reference, Review Focus 2); `test_misaligned_jump_not_learned` (the trapping jump at P runs twice: both trap, Review Focus 3).
- [ ] **Step 2: Run, expect failures:** `make unit T=pipeline`, `make unit T=csr`.
- [ ] **Step 3: RTL.** Look up `bpred` with `pc_f_q` only: on a fetch from `pc_f_q`, `pc_f_q` becomes `target_o` if `taken_o`, else `pc_f_q + 4`; the fetch at `redirect_pc` (the cycle of an EX redirect) is not predicted, which keeps the lookup off the redirect path. The prediction (taken, target) travels with the instruction (`out_q`, fetch queue, ID, EX). EX mispredicts when the actual direction differs, when the actual target differs from the predicted one, or when a non-control instruction was predicted taken; the redirect goes to the target, or to pc + 4 when it was predicted taken but is not. Update `bpred` from EX when a control instruction advances out of EX and is not flushed; a jump that traps (cause 0) does not update. `BP = 0` ties `taken_o` to 0 and removes the unit (generate).
- [ ] **Step 4: Run** `make unit` (all), `make regress CORE=pipe`, `make fuzz CORE=pipe`, `MEM_STALL=0.3 make fuzz CORE=pipe`, `make riscv-tests` (also `MEM_STALL=0.3`), `make release-check`, `make bench`: all pass.
- [ ] **Step 5: Mutants** for the misprediction compare and the update conditions in `scripts/mutants/core_pipe.py`; `make mutate M=core_pipe`: no survivor.
- [ ] **Step 6: Docs** `core_pipe.md` (parameters, IF with prediction, timing table), `pipeline.svg`; commit (author).

### Task 6: Choose the predictor sizes, record Sol 2

**Files:**
- Create: `docs/adr/018-branch-prediction.md`, `docs/results/sol2.json`, `docs/results/sol2-energy.json`
- Modify: `docs/spec.md` (timing table, parameters), `docs/verification.md` (new tests, mutation table), `docs/modules/overview.md` (area), `docs/design/sol2.md` (results)

- [ ] **Step 1: Measure** `BTB_ENTRIES` ∈ {8, 16, 32} with `BHT_ENTRIES = 64`, and `BP = 0`, with the multiplier chosen in task 3: `make bench`, `make energy`, `make area`. Expected (model): CoreMark/MHz about 2.38, 2.40, 2.41 with 8 bits per cycle.
- [ ] **Step 2: Decide** by the EDP per CoreMark iteration; set the defaults. If no size lowers the EDP, `BP` defaults to 0 and the ADR says so.
- [ ] **Step 3: ADR 018** with the table, date and commit; ADR 013 gets an `Update:` line (branches can be predicted).
- [ ] **Step 4: Record Sol 2.** `make bench CORE_NAME="Sol 2" COMMIT=<hash> BASELINE=docs/results/sol1.json` → `docs/results/sol2.json`; energy → `docs/results/sol2-energy.json`. Update spec, verification, overview, the design doc; give the author the README text (CPI, CoreMark/MHz, area).
- [ ] **Step 5: Commit (author)**, then tag `v0.2.0` after CI is green (commands for the author).
