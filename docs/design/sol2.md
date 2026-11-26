# Sol 2: design

Sol 2 is the second generation of the in-order core (ADR 015): Sol 1 (`core_pipe`, release v0.1.0)
with a faster multiplier and branch prediction, chosen by measurement. Each step gets its own ADR with
the numbers before and after.

## Goal and rule

Energy efficiency and speed first, area second (author's goal). A step is accepted when the
**energy-delay product per CoreMark iteration** (energy per iteration × cycles per iteration) goes
down. This is the author's rule in one number: a step that makes the core *x* % faster and costs *x* %
more energy per iteration leaves the product unchanged and is rejected; a step that is faster and also
saves energy lowers it twice. Area is reported for every step and decides between options that are
close.

## Starting point (measured 2026-11-26, Sol 1 release build, BRAM, `make coremark`, 10 iterations)

CoreMark/MHz 1.360, CPI 2.23. Of the 4.07 million cycles above one per instruction: `MULW` in the
iterative unit 76 % (94 thousand, about 33 extra cycles each), taken branches and jumps 18 % (2 cycles
each), load-use 5 %.

A trace model (the commit trace of this run, cycles = instructions + penalties; it reproduces the
measured cycles within 0.1 %) estimates:

| Change | CoreMark/MHz (model) |
|---|---|
| Multiplier with 4 bits per cycle (`MULW` in 10 cycles) | 1.95 |
| Multiplier with 8 bits per cycle (6 cycles) | 2.11 |
| 8 bits + BHT of 64 entries, no BTB (target computed in ID) | 2.25 |
| 8 bits + BHT 64 + BTB 16 + return stack 8 | 2.40 |
| 8 bits + BHT 64 + BTB 64 + return stack 8 | 2.44 |

These are estimates for choosing what to build, not results.

## Benchmarks

`make bench` (`scripts/bench.py`) builds and runs 29 benchmarks and reports the cycles of the part
each benchmark times itself; `BASELINE=<json>` prints the speedup per benchmark and their geometric
mean. Every benchmark checks its own result, and a run counts only if that check passes.

| Suite | Integer | Floating point (soft-float) | Source |
|---|---|---|---|
| CoreMark | coremark | | `third_party/coremark`, port in `sw/coremark` |
| riscv-tests | dhrystone, median, memcpy, multiply, qsort, rsort, towers, vvadd | spmv | `third_party/riscv-tests/benchmarks`, support in `sw/bench/rt_support.c` |
| Embench-IoT 1.0 | aha-mont64, crc32, edn, huffbench, matmult-int, nettle-aes, nettle-sha256, nsichneu, picojpeg, qrduino, sglib-combined, slre | cubic, minver, nbody, st, statemate, ud, wikisort | `third_party/embench-iot`, board support in `sw/bench/embench` |

The cores have no FPU, so the floating-point benchmarks run on a soft-float library
(`scripts/softfp.py`): the soft-float arithmetic of compiler-rt 19.1.7 and five libm functions of fdlibm
from newlib 4.5.0, built from the release archives pinned by SHA-256, plus our glue
(`sw/softfp/libm_shim.c`). They measure floating point emulated in software, which is what a core
without an FPU does; a core with one (Naos, Spica) will be compared on the same programs. `SOFTFP=0`
runs only the integer benchmarks. Left out: the multi-thread and vector riscv-tests benchmarks (the
vector ones return with the vector unit, stage 3). The small C library (`sw/lib`) uses plain byte
loops, so string-heavy results (Dhrystone) are lower than published figures that use an optimized
library.

The results of each core are kept in `docs/results/<core>.json` with the date, the RTL commit, the
compiler and the flags, so that later cores are compared with the same programs: `docs/results/sol1.json`
is the baseline.

Sol 1, release build, BRAM, 2026-11-26, RTL `3c7be96`: CoreMark/MHz 1.360, Dhrystone 0.706 DMIPS/MHz;
CPI of the measured part from 1.07 (`rt-rsort`, `eb-nettle-sha256`) to 6.96 (`eb-edn`,
multiplication); the soft-float ones from 1.22 (`statemate`) to 2.28 (`ud`). Soft-float multiplies
53-bit mantissas, which never take the short path of the multiplier (ADR 011): every such `mul` costs
66 cycles, so the multiplier dominates these too.

The trace model over the 21 integer benchmarks (whole programs, from the commit trace of each),
speedup over Sol 1:

| Change | Geometric mean | Range |
|---|---|---|
| Multiplier 4 bits per cycle | 1.17 | 1.00 (no multiplications) to 2.49 (`edn`) |
| Multiplier 8 bits per cycle | 1.22 | 1.00 to 3.31 |
| 4 bits + BHT 64 + BTB 16 + return stack 8 | 1.34 | |
| 8 bits + BHT 64 + BTB 16 + return stack 8 | 1.40 | 1.02 (`nettle-aes`) to 3.65 (`edn`) |

Half of the benchmarks have no multiplications, so the suite also shows what the predictor alone
gives. The energy measurement (step 0) runs on CoreMark and on a few benchmarks chosen to cover both
kinds (for example `edn` and `nettle-aes`).

## Steps

**0. Energy measurement** (`make energy`, new). Sol 1 release netlist from Yosys (`abc -g cmos2`, as
for the floorplan), simulated with Verilator on CoreMark with toggle counting. Energy per iteration is
estimated as the sum over nets of transitions × load (fanout of the net, in gate inputs), plus the
clock: every flip-flop is clocked every cycle (no clock gating yet). The result is in relative units:
good for comparing Sol 1 and Sol 2, not joules. The model and its assumptions get their own ADR.

**1. Multiplier** (ADR). `muldiv` gets a parameter for the multiplier bits per cycle (1, 4, 8). Each
cycle adds 4 or 8 partial products to the accumulator through a carry-save tree and one adder. The
divider stays one bit per cycle (CoreMark divides 78 times in 3.3 million instructions). The short path
of ADR 011 is kept. Both 4 and 8 bits are built and measured (author's decision); the one with the lower
energy-delay product is the default, the other remains a parameter value.

**2. Branch prediction in fetch** (ADR).
- A BHT of 2-bit counters indexed by the pc (64 entries to start).
- A small BTB of 16 entries: tag and target. To save flip-flops, only the low bits of the target are
  stored where the measurement shows the targets stay near the branch.
- A return stack of 8 entries for JAL/JALR with `ra`.
- A wrong prediction is found in EX, as today: 2 cycles. A correct taken branch costs 0 cycles.
- The BHT and BTB sizes are parameters; 8, 16 and 32 BTB entries are measured.

Steps 1 and 2 change the microarchitecture, so the result is **Sol 2**. The `s` variant (ADR 015) may
leave out the predictor.

## Interfaces

- `muldiv` keeps its ports; only the latency changes.
- The predictor sits in IF and is updated from EX. The memory ports, the commit trace and the CSRs do
  not change; a prediction never changes the architectural state, only the cycle count.

## Verification

Everything that checks Sol 1 checks Sol 2. In particular:
- `test_muldiv` for every multiplier width;
- the pipeline tests, with their exact cycle counts updated for prediction, plus new tests: loop branch
  learned, BTB alias, return stack overflow, misprediction recovery;
- Spike comparison and random programs, also with a stalling memory;
- `riscv-tests`, `release-check`;
- mutation testing of `muldiv` and `core_pipe`, and new mutants for the predictor;
- XLEN = 32 builds and passes.

## Not in Sol 2

Removing the load-use cycle (model: +6 %), the critical path (from the MEM register through forwarding and
the branch comparison to the fetch address and its pc + 4 adder), clock gating of idle registers, a 33 × 33 multiplier
(planned for Spica and Naos). Each may come later with its own measurement.

## Afterwards

A short note on the interfaces an out-of-order core will need from the cache and the bus (ADR 015),
before stage 2 designs them.
