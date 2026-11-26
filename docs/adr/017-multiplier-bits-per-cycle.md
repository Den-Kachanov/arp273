# ADR 017: Four multiplier bits per cycle

## Context

On Sol 1 (ADR 009, ADR 011) a multiplication takes one multiplier bit per cycle: a `MULW` costs 34
cycles. CoreMark spends 76 % of its cycles above one per instruction waiting for its 94 thousand
`MULW` ([design of Sol 2](../design/sol2.md)), and on several Embench programs the multiplier is the
largest cost too (`edn` has a CPI of 6.96). Sol 2 accepts a change only if it lowers energy × delay per
CoreMark iteration (ADR 015, ADR 016).

## Options

| Option | Pros | Cons |
|---|---|---|
| A. One bit per cycle (Sol 1) | Smallest; the adder is shared with the divider | 34 or 66 cycles per multiplication |
| **B. `MUL_BITS` bits per cycle, `MUL_BITS` partial products added in one sum per iteration** | Iterations / `MUL_BITS`; the divider and the protocol stay as they are; one parameter covers 1, 2, 4, 8 | A longer path through the sum; more area |
| C. A combinational or pipelined 33 × 33 multiplier | 1 to 3 cycles | About 65 thousand transistors, half of the core (estimate); kept for Naos and Spica (ADR 015) |

## Decision

Option B, `muldiv #(MUL_BITS)` and `core_pipe #(MUL_BITS)`, default **4** (Sol 2). `MUL_BITS = 1` is
Sol 1 gate for gate (the shared adder is kept for it). How the unit computes: [modules/muldiv.md](../modules/muldiv.md).

The decision is made on energy × time, not energy × cycles. Eight bits per cycle gives the fewest
cycles, but its sum of eight partial products becomes the longest path of the core, so one CoreMark
iteration takes more time than on Sol 1.

## Measurements

2026-11-26, release build of `core_pipe`, BRAM, RTL of commit `ceb1f81` (Sol 1 is its configuration `MUL_BITS = 1`, identical to `3c7be96`).
Benchmarks: `make bench` (29 programs, GCC 16.2.0 `-O2`); energy: `make energy` (ADR 016); area and
delay: `scripts/area.py --xc7 --release` (Yosys 0.66, one run per width; the delay is the logic-only
arrival time of `sta` on xc7, without routing, not Fmax). All numbers: `docs/results/mul-bits.json`.

| `MUL_BITS` | 1 (Sol 1) | **4** | 8 |
|---|---|---|---|
| CoreMark/MHz | 1.360 | **1.961** | 2.117 |
| Geometric mean of speedups, 29 benchmarks | 1.000 | **1.215** | 1.270 |
| Cycles per CoreMark iteration | 736 207 | **510 631** | 472 466 |
| Energy per iteration (ADR 016 units) | 1.127 × 10¹⁰ | **0.941 × 10¹⁰** | 0.932 × 10¹⁰ |
| Energy × cycles | 8.30 × 10¹⁵ | 4.81 × 10¹⁵ | 4.41 × 10¹⁵ |
| Logic delay of the core, xc7 | 5650 ps | **6155 ps** | 9339 ps |
| Time per iteration (cycles × delay) | 4.16 ms | **3.14 ms** | 4.41 ms |
| Energy × time (relative) | 4.69 | **2.96** | 4.11 |
| `muldiv`: LUT6 / flip-flops | 953 / 211 | 1738 / 210 | 2347 / 210 |
| Core: LUT6 / flip-flops | 5802 / 3741 | 6572 / 3740 | 7229 / 3740 |
| Core: transistors (CMOS estimate) | 127 320 | 146 394 | 164 526 |
| Core on xc7: LUT / CARRY4 | 4238 / 184 | 4612 / 269 | 4840 / 337 |
| Logic delay of `muldiv` alone, xc7 | 3885 ps | 5919 ps | 9339 ps |

Speedups of the geometric mean range from 1.00 (no multiplications) to 2.5 (`edn`). The trace model of
the design (1.95 and 2.11 CoreMark/MHz) matched the measured 1.961 and 2.117.

What the numbers show:
- **Shorter multiplications also save energy.** With 4 bits per cycle one CoreMark iteration costs 16.5 %
  less energy, and energy × time falls by 37 %: the 3741 flip-flops are clocked for 226 thousand fewer
  cycles. The clock term alone falls from 0.551 × 10¹⁰ to 0.382 × 10¹⁰; the logic falls a little too
  (0.577 to 0.559 × 10¹⁰), because a pipeline waiting for the multiplier is not free either.
- **Eight bits per cycle is slower in time.** Its 8 partial products in one cycle make `muldiv` the
  critical path (9339 ps); a clock set by that path makes every instruction slower, not only the
  multiplications.
- **The area cost is moderate:** +13 % LUT6 and +15 % transistors for the core; no flip-flops added.

## Consequences

- Sol 2 multiplies 4 bits per cycle: `MULW` and short `MUL` 10 cycles (34 before), full-width `MUL` and
  `MULH*` 18 (66). Division is unchanged.
- `MUL_BITS = 8` stays available. It would pay off if place-and-route showed a longer path elsewhere,
  or with a multiplier split over two stages.
- The tests run for `MUL_BITS` 1, 4 and 8; `tb/unit/test_core.py` has the configurations `sol1`
  (`MUL_BITS = 1`, `BP = 0`) and `sol2`.
- The ordering of the measured delays (5650 ps for Sol 1 here) differs from earlier runs (5484 ps in
  ADR 013): ABC moves numbers between runs, so only numbers from the same run are compared. The same
  holds for energy: two syntheses of the same configuration differed by about 1 % (0.941 here,
  0.952 × 10¹⁰ in ADR 018).
- The energy numbers were corrected after the final review (ADR 016: the clock had been counted twice);
  the first version said −21 % energy and −41 % energy × time.
