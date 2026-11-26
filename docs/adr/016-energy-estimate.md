# ADR 016: Energy estimate from the switching activity of the gate netlist

## Context

From Sol 2 on, a change to a core is accepted only when it lowers the energy-delay product per CoreMark
iteration ([design of Sol 2](../design/sol2.md), ADR 015). Cycles are measured exactly by the
counters; energy needs a model, because there is no silicon, no standard-cell library installed and no
FPGA board yet.

What is needed is not joules but a number that moves the right way when the design changes: more
toggling logic, more flip-flops clocked every cycle, or more cycles for the same work must all cost
more.

## Options

| Option | Pros | Cons |
|---|---|---|
| A. Count cycles only | Exact, free | Ignores that a bigger unit burns more per cycle: an 8-bit-per-cycle multiplier would always win |
| B. Toggles of the RTL signals (Verilator coverage on `core_pipe`) | Fast | A register bit and a 64-bit adder output count the same; the logic between registers is invisible |
| **C. Toggles of every net of the gate netlist, weighted by the load each net drives, plus the clock of every flip-flop** | Sees the logic between registers; a wide unit costs in proportion to its gates; all from tools already used (Yosys `abc -g cmos2` as for the floorplan, Verilator) | Relative units; no wire capacitance, no leakage, no glitches (Verilator evaluates each net once per cycle) |
| D. Vendor power tools (Vivado with a SAIF file) or a standard-cell flow (OpenLane, sky130) | Real capacitances | Not installed (disk space, the author's decision); slower; not before stage 2 |

## Decision

Option C, `make energy` (`scripts/energy.py`):

1. **Netlist.** The release build (`DEBUG = 0`) with the parameters under test, flattened and mapped to
   NAND, NOR and NOT gates (`abc -g cmos2`). Every net gets a plain name (`rename -enumerate`), so the
   Verilog netlist and the JSON netlist (for the fanout) name it the same way.
2. **Simulation.** The netlist runs in the same harness as the RTL (`tb/core/main.cpp`, BRAM) with
   `--coverage-toggle --coverage-underscore`: the 0→1 and 1→0 transitions of every net bit are counted.
   The harness writes them to `COVERAGE_FILE` at the end of the run.
3. **Model**, in relative units:

   ```
   logic = Σ over net bits  transitions × (fanout + 1)      fanout: cell inputs the bit drives
   clock = 2 × flip-flops × cycles                          two clock edges; no clock gating yet
   energy = logic + clock
   ```

   `+ 1` stands for the gate's own output and the wire. A bit with several names (an output port and
   the wire behind it, or the `reg` that `write_verilog` gives a flip-flop) is counted once. The clock
   and reset nets (`clk_i`, `rst_ni`) are left out of the logic sum: they reach every flip-flop, and as
   logic they would count the clock a second time.
4. **Self-checks.** Bit 0 of the cycle counter must toggle once per cycle and bit 1 once every two
   cycles (±2). The coverage file must name at least 95 % of the net bits of the netlist and at most 1 %
   names the netlist does not have; otherwise the script stops. Verilator skips names starting with
   `_` unless `--coverage-underscore` is given (the first attempt covered 5 thousand of 37 thousand
   bits), and it writes `[` and `]` inside a name as `__05b` and `__05d`, which the script decodes. With
   both handled, every net bit of the Sol 1 netlist is matched. The model is unit-tested on a small
   made-up netlist (`tb/unit/test_energy_model.py`).
5. **Per CoreMark iteration.** CoreMark is built with 1 and with 2 iterations; one iteration is the
   difference, so start-up and the printed report cancel out. The energy-delay product is energy ×
   cycles of one iteration.
6. **Programs.** Counting every net costs time: the gate-level simulation runs at about 2 thousand
   cycles per second (one CoreMark iteration takes about 6 minutes), so `make energy` uses short
   programs: CoreMark, `rt-qsort` (branches, no multiplications) and `rt-dhrystone`. The long Embench
   programs (`edn`, 24 million cycles) stay with `make bench`, which counts cycles only.

## Measurements

Sol 1 (`core_pipe`, release build, BRAM), RTL commit `3c7be96`, 2026-11-26, Yosys 0.66, Verilator 5.052,
`make energy`; `docs/results/sol1-energy.json`. The netlist: 127 320 transistors (CMOS estimate), 3741
flip-flops, 37 676 net bits.

| Run | Cycles | Logic | Clock | Energy | Per cycle |
|---|---|---|---|---|---|
| CoreMark, 1 iteration (whole program) | 765 176 | 0.607 × 10¹⁰ | 0.573 × 10¹⁰ | 1.179 × 10¹⁰ | 15 409 |
| CoreMark, 2 iterations | 1 501 383 | 1.183 × 10¹⁰ | 1.123 × 10¹⁰ | 2.306 × 10¹⁰ | 15 362 |
| **One CoreMark iteration** | **736 207** | 0.577 × 10¹⁰ | 0.551 × 10¹⁰ | **1.127 × 10¹⁰** | 15 313 |
| `rt-qsort` (whole program) | 321 786 | 0.408 × 10¹⁰ | 0.241 × 10¹⁰ | 0.648 × 10¹⁰ | 20 151 |
| `rt-dhrystone` (whole program) | 407 874 | 0.467 × 10¹⁰ | 0.305 × 10¹⁰ | 0.773 × 10¹⁰ | 18 942 |

One iteration takes 736 207 cycles, the same as `make bench` measures (7 355 100 cycles for 10
iterations).

What it shows about Sol 1:
- The clock of the flip-flops is half of the energy (49 % on CoreMark): every one of the 3741
  flip-flops is clocked in every cycle, also while the pipeline waits for the multiplier. Clock gating
  (later) attacks this half.
- CoreMark spends less energy per cycle (15.3 thousand) than `qsort` (20.2 thousand): 76 % of its extra
  cycles are spent waiting for `MULW`, and a waiting pipeline toggles little logic but still clocks
  every flip-flop. Shorter multiplications should therefore save energy, not only time.

**A correction.** The first published numbers counted the clock twice: the clock net was also in the
logic sum (found in the final review of Sol 2). They also missed about 3 600 net bits whose names
Verilator had encoded. The numbers above and in ADR 017 and ADR 018 are recomputed from the same
simulations with the corrected script (`--reuse`); the decisions did not change.

## What the model does not show

- **Wire capacitance and placement.** A net to a distant block costs the same as a local one.
- **Glitches.** Verilator settles each net once per cycle; real gates may toggle several times while
  the inputs arrive (multipliers and adders glitch most). The model therefore favours wide
  combinational logic slightly.
- **Leakage.** Static power grows with area and time; it is not counted, so the model favours a large
  but quiet design. Area is reported separately for that reason.
- **Memories.** The register file is counted as flip-flops; a real chip would use a memory macro.
- **Clock gating.** Every flip-flop is counted as clocked every cycle. Clock gating would lower the
  clock term of idle units; when it is added, the clock term must count only enabled cycles.

The numbers compare designs measured the same way; they are not joules and are not comparable with
other projects.
