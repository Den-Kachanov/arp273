# ADR 004: The register file returns the new value when one register is written and read in the same cycle (write-through)

Status: draft, pending author review

## Context

In the pipeline (stage 1, week 2) the instruction in WB writes a register, and the instruction in ID may read it in the same cycle. We need to decide what the read sees: the old value or the new one. This decides whether a separate forwarding path from WB is needed.

## Options

| Option | Pros | Cons |
|---|---|---|
| **A. Write-through: a read returns `wdata_i` if `we_i` and `waddr_i == raddr_i` and the address is not 0** | No forwarding from WB; simpler datapath | Two 5-bit comparators and an `XLEN`-bit mux per read port; the mux sits after the array read |
| B. A read returns the old value | Nothing extra in the regfile | The datapath needs forwarding from WB: the same muxes plus control logic, and one more place for bugs |

Area and delay of the options were not measured; the claim that the hardware is the same is reasoning.

## Decision

Option A. The read is combinational, the write happens on the clock edge. Address 0 always reads 0, even when x0 is written in the same cycle.

## Consequences

- Write-through only makes sense when the write and the read belong to different instructions, that is, in a pipeline. In the single-cycle core the write and the read of one register in one cycle belong to the same instruction (for example `ADDI x1, x1, 1`): the bypass would return the result instead of the old value and create a combinational loop through the ALU. Therefore `regfile` has a `WRITE_THROUGH` parameter (default 1); `core` sets it to 0 (ADR 008).
- The datapath only needs forwarding from EX/MEM, not from WB.
- The regfile test must check this case separately, including x0.
- If the mux ends up on the critical path of the ID stage, the decision is revisited in a separate ADR with before/after measurements.
