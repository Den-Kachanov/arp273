# ADR 015: A family of cores named after classes of stars

## Context

Stage 1 ended with one core: the in-order five-stage pipeline `core_pipe` (ADR 013, ADR 014). Before
optimizing it, the author set the goal for all further CPU work: **energy efficiency and speed first,
area second**. A change that makes the core faster but costs as much or more in energy per unit of
work is not worth it; a change that improves efficiency more than speed is the best kind.

No single microarchitecture is best for that goal at every level of performance:
- Dynamic energy per operation grows roughly with C·V², and a higher clock needs a higher supply
  voltage. To match a faster core, a simple core has to run at a higher clock and voltage, and can
  spend more energy than the faster core running slower.
- At low performance the opposite holds: the extra structures of an out-of-order core (renaming,
  issue queues, reorder buffer) switch every cycle and buy nothing.

So each microarchitecture is best on its own part of the energy–performance curve. This is why phones
and laptops combine several kinds of cores. Apple uses out-of-order designs for both its performance
cores and its efficiency cores (the efficiency cores are narrower). Arm's smallest application cores
(Cortex-A53, A55, A510, A520) are in-order. Small in-order microcontroller-class cores run the
always-on tasks next to them.

The project also needs a core per GPU lane group (stage 4). That is a small in-order RV32 design.

## Options

| Option | Pros | Cons |
|---|---|---|
| A. One core, optimized for everything | One design, one verification | Fits one point of the curve only; no story about trade-offs |
| B. In-order core plus two separate out-of-order designs | Each tuned freely | Two out-of-order cores are more work than one author can verify in the time available |
| **C. One parameterized out-of-order design in two configurations, the in-order core, and a SIMT core built from the in-order core's modules** | Two points on the curve for the price of one out-of-order design; the GPU reuses modules that are already verified and mutation-tested | The narrow configuration is not tuned as freely as a separate design |

## Decision

Option C. Every core is named after a class of stars in the spectral sequence O, B, A, F, G, K, M.
The sequence runs from massive, hot, very luminous and short-lived stars (O) to small, cool and
long-lived ones (M), as the cores run from fast and power-hungry to small and frugal. Each core also
takes the name of a real star of its class, so that a name is not a bare letter (an "M-core" would be
confused with Motorola's M·CORE architecture and with Arm's Cortex-M).

| Core | Class | Named after | Microarchitecture | ISA | Goal |
|---|---|---|---|---|---|
| **Naos** | O | ζ Puppis, a blue supergiant of type O4 If(n)p, one of the hottest and most luminous stars visible to the naked eye | Wide out-of-order | RV64 | Highest performance |
| **Spica** | B | α Virginis, an early B-type star (B1 III-IV in the Bright Star Catalogue), far smaller than a supergiant | Narrow out-of-order: the RTL of Naos with smaller parameters (issue width, reorder buffer, queues, physical registers) | RV64 | Performance at lower energy |
| **Sol** | G | The Sun, type G2V | In-order, five stages: `core_pipe` (ADR 013, ADR 014) | RV64 | Lowest energy per operation at its performance |
| **Proxima** | M | Proxima Centauri, a red dwarf of type M5.5, the nearest star to the Sun | SIMT core: Sol's decoder, ALU and LSU, one shared front end, several execution lanes, warp control | RV32 + GPU thread-control instructions | Throughput per area and per joule |

The GPU as a whole is **Hyades**: an open cluster, the nearest one to the Sun (about 153 light-years),
many stars that formed together and move together, as the Proxima cores run the same instructions
together. (Pleiades, the better-known cluster, is the name of a NASA supercomputer.)

Naos, Spica and Sol implement the same ISA, so that a program can move between them. Proxima has its
own ISA subset, because it runs GPU kernels, not the operating system.

The free classes A, F and K are left for later cores, for example a K-class always-on microcontroller
core.

Rules for using the names:
- The RTL keeps its module names for now (`core_pipe` is Sol); a rename, if any, is a separate change.
- Documents say "Sol (`core_pipe`)" at the first mention.
- "Alpha", "Beta" and "Gamma", used while the family was being discussed, are not used.

## Generations and variants

A full core name is `<star> <generation>[<variant letters>]`, for example `Sol 1`, `Sol 2s`,
`Spica 1v`.

**Generation** (a number) changes when the microarchitecture changes: a different pipeline, a new
branch predictor, a different multiplier structure, a new cache. Each generation is measured on the
same curve (CoreMark per MHz, logic delay, energy per iteration, area) and the step is recorded in an
ADR. Changes that keep the microarchitecture (bug fixes, a shorter path, fewer flip-flops) stay in the
same generation. The repository version (`v0.1.0`, ...) is separate: it numbers releases of the whole
project, not cores.

**Variants** (letters after the generation) are configurations of the same RTL of that generation,
selected by parameters. A variant never needs its own design; if it would, it is a new core or a new
generation. The letters:

| Letter | Meaning | Example |
|---|---|---|
| `s` | small: optional parts left out (performance counters other than `mcycle`/`minstret`, the divider, the debug trace) | `Sol 2s` |
| `v` | with the vector unit (stage 3) | `Sol 2v` |
| `-32` | RV32 instead of RV64 (`XLEN = 32`); written last, after a hyphen, because it is a number | `Sol 1-32`, `Sol 2s-32` |

Letters combine in the order of the table (`Sol 2sv`). New letters are added to this table when a new
parameter appears.

**Today:** the core of release v0.1.0 is **Sol 1** (`core_pipe` at commit `3c7be96` RTL). Proxima 1
will be designed from Sol's modules in stage 4.

## Facts checked for the names

Checked on 2026-11-26:
- Arp 273, after which the project is named, is a pair of interacting galaxies (UGC 1810 and UGC
  1813) in the constellation Andromeda.
- Naos (ζ Pup) and Spica (α Vir Aa) are names approved by the IAU Working Group on Star Names in 2016.
  The spectral type of Spica's primary is given differently by different studies; the class B is not
  in doubt.
- Red dwarfs are the most common stars in the Milky Way (estimates of their share range from 70 % to
  90 %); the smallest live for trillions of years, against about 10 billion for the Sun. O-type stars
  stay on the main sequence for only a few million years.
- No processor or GPU named Naos, Spica, Proxima or Hyades was found.

Sources: Wikipedia articles "Arp 273", "Zeta Puppis", "Spica", "Proxima Centauri", "Red dwarf",
"O-type star", "Hyades (star cluster)", "M·CORE", "Pleiades (supercomputer)"; WGSN Bulletin 2.

## Consequences

- **Optimization of Sol** (the next work) has a clear target: energy per operation and small area. The
  area matters more than for a single core, because the GPU repeats Proxima many times and Proxima
  is built from Sol's modules. For example, a fast 33 × 33 multiplier suits Naos and Spica as a separate
  functional unit; Sol gets a cheaper iterative one with more bits per cycle. Each such choice is
  measured and recorded in its own ADR.
- **Measurements compare the cores on one curve:** CoreMark per MHz, logic delay, energy per CoreMark
  iteration (estimated from switching activity of the gate netlist), area.
- **Order (author's decision, 2026-11-26):** Sol 2 first, then stage 2 (SoC, cache, FPGA), the vector
  unit, Proxima and Hyades. **Spica 1** is built in stage 5a (July–August 2027) if Hyades is ready on
  time, from the reserve of that stage; **Naos 1** comes after September 2027. Out-of-order execution
  needs its own verification: renaming, recovery from mispredicted branches, precise exceptions under
  speculation. The in-order commit lets the Spike commit-log comparison work unchanged.
- **Interfaces for out-of-order cores** are written down briefly before stage 2 designs the cache and
  the bus, so that Spica can use them later (more than one outstanding request, a predictor interface
  in fetch).
- Proxima is designed in stage 4 together with the GPU specification.
