# Branch predictor (`rtl/core/bpred.sv`)

Diagram: [img/bpred.svg](../img/bpred.svg). Decisions and measurements: ADR 018. Used by the fetch stage of
the pipelined core (Sol 2, parameter `BP`).

The predictor only guesses. EX checks every guess (direction and target, and whether the instruction
is a control instruction at all), so a wrong or stale entry costs cycles, never correctness. This is
why the tags and the stored targets may be partial.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `BHT_ENTRIES` | 64 | 2-bit counters, indexed by `pc[2 +: log2(BHT_ENTRIES)]` |
| `BTB_ENTRIES` | 16 | Direct-mapped BTB, indexed by `pc[2 +: log2(BTB_ENTRIES)]` |
| `RAS_DEPTH` | 8 | Return stack entries (circular: an overflow overwrites the oldest) |
| `TAG_BITS` | 10 | pc bits above the BTB index that are compared (a partial tag may alias) |
| `TARGET_BITS` | 20 | Low target bits stored in the BTB and the stack; the bits above come from the pc. A taken transfer whose target differs from its pc above these bits is not written |

All sizes are powers of two. With the defaults the state is 16 × 31 + 64 × 2 + 8 × 18 + 3 = 771 bits.

## Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `clk_i`, `rst_ni` | input | 1 | Clock, synchronous reset (only the BTB valid bits and the stack pointer) |
| `pc_i` | input | `XLEN` | Fetch address |
| `taken_o` | output | 1 | BTB hit and: a jump, a call, a return, or a branch whose counter is 2 or 3 |
| `target_o` | output | `XLEN` | BTB target, or the top of the stack for a return |
| `accept_i` | input | 1 | The fetch at `pc_i` was accepted: a predicted call pushes `pc_i + 4`, a predicted return pops |
| `upd_i` | input | 1 | A control instruction was resolved in EX |
| `upd_pc_i` | input | `XLEN` | Its pc |
| `upd_kind_i` | input | 2 | 0 branch, 1 jump, 2 call (`rd` = x1 or x5), 3 return (JALR with `rs1` = x1 or x5, `rd` = x0) |
| `upd_taken_i` | input | 1 | Taken |
| `upd_target_i` | input | `XLEN` | Its target |

The lookup is combinational from `pc_i`; updates and stack moves happen at the clock edge.

## Behaviour

- **BTB write:** a taken update whose target fits (or any return: its target comes from the stack)
  writes valid, tag, the low target bits and the kind.
- **Counters:** a branch update changes its counter (saturating at 0 and 3) when the BTB holds the
  branch; a branch written into the BTB for the first time starts at 2 (weakly taken). A counter is
  therefore never read before it is written, and the BHT needs no reset.
- **Return stack:** no repair after a misprediction; a wrong return target is found in EX like any other.

## Testing

`tb/unit/test_bpred.py`, `make unit T=bpred`, XLEN = 64 and 32: reset clears every entry (the valid bits
and tags are set to matching garbage before the reset), a branch learns and its counter saturates at
both ends, a never-taken branch gets no entry, aliasing entries replace each other, a far target is not
written, jumps are always taken, call and return, stack overflow, a lookup without `accept_i` moves
nothing. `make mutate M=bpred`: 17 mutants, all caught (the first run found four gaps in the tests,
each closed by a test).
