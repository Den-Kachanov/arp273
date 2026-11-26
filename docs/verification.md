# Verification plan

How the CPU core is verified, what each method proves, what is covered today and what is not. The contract
being verified is the [core specification](spec.md). Every method below runs from the `Makefile`, most of
them in CI on every push (`.github/workflows/ci.yml`).

## Strategy

Four independent views of the same design. A bug has to get past all of them:

1. **Unit tests (cocotb):** every module alone, against hand-written expectations and Python reference
   models, with edge values (0, 1, −1, the smallest and largest numbers) and random values. They show
   *where* a bug is.
2. **Comparison with Spike:** whole programs on the whole core. Every retired instruction (pc, instruction,
   register write, store) must match the RISC-V reference simulator, and the comparison stops at the first
   difference. It shows *that* the core agrees with the ISA on real code.
3. **Compliance (`riscv-tests`):** self-checking tests written by others from the specification. They catch
   what my own tests share with my own misreading of the specification.
4. **Checking the checks:** mutation testing, deliberate bugs, random initial state and random memory
   timing. They show that the tests above would fail if the RTL were wrong.

## Methods

| Method | What it shows | Command | CI |
|---|---|---|---|
| Unit tests, 13 files, 109 tests, XLEN = 64 and 32; the multiplier for `MUL_BITS` 1, 4, 8; the core as Sol 1 and Sol 2 | Each module meets its interface description | `make unit` | yes |
| Pipeline hazard tests (`test_pipeline.py`) | Forwarding, load-use, branches, flushes, M unit in the pipeline, branch prediction (exact flush counts, targets that change in the low or only in the upper bits, returns to several sites); exact cycle counts on a BRAM, identical traces on three randomly stalling memories | `make unit T=pipeline` | yes |
| CSR and exception tests (`test_csr.py`) | Every exception cause with `mepc` and `mtval`, CSR read-modify-write, `mstatus` through trap and MRET, FENCE.I, abort of the divider; a stale BTB entry after FENCE.I, an exception on the predicted path, a branch dropped by FENCE.I | `make unit T=csr` | yes |
| Spike comparison, C programs (`sw/*.c`, 5 programs; `sw/a/*.c`, programs with atomics, pipelined core only) | Both cores retire exactly what Spike retires | `make regress`, `make regress CORE=pipe` | yes |
| Spike comparison, random programs (`scripts/gen_prog.py`) | The same on generated code: every RV64IM computation instruction, edge operands, all load/store sizes, all branch conditions, JALR with bit 0 set, division by zero and MIN / −1 | `make fuzz SEEDS="1 2 3"` | yes, 16 seeds per core, 8 more with a stalling memory |
| Random memory timing | The pipeline is correct for any latency the protocol allows; the memory models also check the protocol (one outstanding request, aligned addresses) | `MEM_STALL=0.3 make fuzz CORE=pipe` | yes |
| Random initial state ([ADR 012](adr/012-chip-readiness-checks.md)) | Nothing depends on registers without reset starting at 0, as they would not on a chip | always on (`--x-initial unique`); `X_SEED` changes it | yes |
| `riscv-tests` `rv64ui-p`, `rv64um-p`, `rv64ua-p` | Compliance of RV64I, M, A, and the CSR and trap path their startup code uses | `make riscv-tests` | yes, also with `MEM_STALL=0.3` |
| Benchmarks (`make bench`): CoreMark, 9 riscv-tests benchmarks, 19 Embench-IoT programs (8 of the 29 in soft-float) | Every program checks its own result; a wrong result fails | `make bench` | yes, the 21 integer ones |
| Release build check | The release build (`DEBUG = 0`) behaves like the debug build: same exit code, cycles and retired instructions | `make release-check` | yes |
| Mutation testing | The tests fail when the RTL is changed in a plausible wrong way | `make mutate M=<module>` | no (minutes per module): run after changing a module |
| Lint | No Verilator warnings with `-Wall`, XLEN = 64 and 32 | `make lint` | yes |
| Structural rules for a chip flow | No latches, no asynchronous resets, no combinational loops, no multiple drivers | `make struct` | yes |
| Synthesis | Every module synthesizes in Yosys | `make synth` | yes |

The commit log comparison (`scripts/compare.py`) compares the pc, the instruction, the register write and
the store (address and value) of every retired instruction. It ignores CSR writes in Spike's log and code
below `0x8000_0000` (Spike's boot ROM). Programs with interrupts or with reads of `mcycle` would differ in
timing-dependent values: none of the compared programs uses them.

## What is covered

| Feature | Unit | Spike | `riscv-tests` | Mutation |
|---|---|---|---|---|
| ALU, W forms, shifts by 6 bits | `test_alu` | C + random | `rv64ui` | `alu` |
| Immediates, all formats | `test_imm_gen` | C + random | `rv64ui` | `imm_gen` |
| Decoder, illegal encodings | `test_decoder` (also 5000 random words) | — | `rv64ui` | `decoder` |
| Register file, x0, write-through | `test_regfile` | C + random | `rv64ui` | `regfile` |
| Branches, JAL, JALR (bit 0 cleared) | `test_branch_unit`, `test_core` | C + random | `rv64ui` | `branch_unit`, `core`, `core_pipe` |
| Loads and stores, every size and sign, byte lanes | `test_lsu`, `test_core` | C + random | `rv64ui` | `lsu` |
| M extension, results and latency, `MUL_BITS` 1, 4, 8 | `test_muldiv` (independent Python model `mext.py`) | C + random | `rv64um` | `muldiv` |
| Pipeline hazards and stalls | `test_pipeline` | C + random, stalling memory | all | `core_pipe` |
| A extension: AMOs, LR/SC, lanes, misaligned atomics; `mhartid` | `test_atomic`, `test_decoder` | `sw/a/atomic.c`, stalling memory | `rv64ua` | `decoder`, `core_pipe`, `csr` |
| Branch prediction (BHT, BTB, return stack, checks in EX) | `test_bpred`, `test_pipeline`, `test_csr` | C + random, stalling memory | all | `bpred`, `core_pipe` |
| Memory protocol | protocol checks in the memory models | stalling memory | stalling memory | `core_pipe` |
| CSRs, exceptions, MRET, FENCE.I | `test_csr`, `test_decoder` | — (programs use none) | startup and pass/fail path; `fence_i` | `csr`, `core_pipe` |
| Misaligned access and jump target | `test_core`, `test_csr` | — | — (`ma_data` not run) | `core`, `core_pipe` |
| Performance counters | `test_perf_counters`, `test_pipeline` | — | — | `perf_counters` |
| Release build | — | — | — | `make release-check` |

## Mutation results

A mutant is a small plausible bug inserted into the RTL (a wrong condition, a missing term, a swapped
signal). A mutant is *killed* when at least one test fails. A mutant is *equivalent* when it cannot change
behaviour; each such case is explained in its mutant file (`scripts/mutants/`).

Runs of 2026-11-25; `core_pipe`, `muldiv` and `bpred` again on 2026-11-26 with Sol 2 (no commit hash;
`core_pipe` mutants run in the three configurations `sol1`, `sol2`, `sol2-bp`). On 2026-11-27, with the A
extension: `decoder` and `csr` in full, the 10 new or changed mutants of `core_pipe` (`--only`):

| Module | Mutants | Killed | Equivalent |
|---|---|---|---|
| `alu` | 22 | 22 | 0 |
| `bpred` | 17 | 17 | 0 |
| `branch_unit` | 8 | 8 | 0 |
| `core` (reference) | 24 | 24 | 0 |
| `core_pipe` | 75 | 74 | 1 |
| `csr` | 19 | 18 | 1 |
| `decoder` | 27 | 26 | 1 |
| `imm_gen` | 14 | 14 | 0 |
| `lsu` | 11 | 11 | 0 |
| `muldiv` | 43 | 43 | 0 |
| `perf_counters` | 8 | 8 | 0 |
| `regfile` | 12 | 11 | 1 |
| **Total** | **280** | **276** | **4** |

No mutant survives. Mutants that survived when they were first written were never accepted as they were.
Each one either led to a new test (for example a store sent right after MRET, two writes to one register
in the pipeline, a JALR whose target changes only in the low bits) or showed redundant logic that was then
removed (the load-use check in ID, a flush term in the EX handshake, two sign-extension gates in the
multiplier, a trap term in the predictor update). The ADRs record each case.

Other checks that the checks work:
- **A deliberate bug in the ALU** (XOR computed as OR) fails `rv64ui-p-xor` and `xori`: the compliance
  tests compare results, not only that the program finishes.
- **Broken copies of the RTL** are caught by the Spike comparison: a write strobe not shifted, a halfword
  sign taken from the wrong bit, BNE as BEQ ([modules/core.md](modules/core.md)).
- **Random initial state** found a real difference from Spike (a callee-saved register saved before being
  written), which led to `crt0` clearing every register ([ADR 012](adr/012-chip-readiness-checks.md)).

## Known gaps

What is not verified today, and how it will be:

| Gap | Risk | Plan |
|---|---|---|
| No functional coverage: there is no measure of which hazard combinations the random programs reach | A rare combination untested | Coverage points in the harness (stage 2), or cocotb coverage |
| Random programs use no CSRs, exceptions or FENCE.I | The trap path is tested only by directed tests and `riscv-tests` | Extend `gen_prog.py` with handled exceptions |
| `rv64mi-p` (machine-mode tests) not run | CSR corner cases | With the timer and interrupts (stage 2) |
| `riscv-arch-test` via RISCOF | Official compliance signature | Stage 2 |
| No formal verification | Bugs outside every test | `riscv-formal` (the core will export RVFI), SymbiYosys properties for the bus protocols and the MSI protocol; planned in stage 2b (ADR 019) |
| Misaligned access (`ma_data`) | Not supported: traps | Only if a handler emulates it |
| No gate-level simulation, no timing | Synthesis-simulation mismatch, Fmax | After FPGA place-and-route |
| The release build is compared by simulation only | Differences on paths no program reaches | A formal equivalence check (Yosys `equiv_*` was inconclusive, ADR 013) |
| Mutation testing is not in CI | A new module without enough tests | Run by hand after every module change; required before a module is "done" |

## When a module is done

A module is done when all of these hold:
- its unit tests pass for XLEN = 64 and 32;
- the Spike regression and the random programs match;
- `make lint` and `make struct` are clean;
- the mutation check has no surviving mutant;
- its interface and a diagram are in `docs/modules`.

## Running everything

```sh
make lint struct unit                            # minutes
make regress && make regress CORE=pipe           # needs Spike and riscv64-elf-gcc
make fuzz CORE=pipe SEEDS="1 2 3 4 5 6 7 8"
MEM_STALL=0.3 make fuzz CORE=pipe
make riscv-tests && MEM_STALL=0.3 make riscv-tests
make release-check
make mutate M=core_pipe                          # about 6 minutes
```
