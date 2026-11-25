# ADR 008: Memory interface of the single-cycle core

Status: draft, pending author review

## Context

The project rules require a valid/ready handshake on memory ports and synchronous reads (compatible with BRAM). The single-cycle core (week 1) fetches an instruction, executes it and reads or writes data in one cycle, so a memory with combinational reads suits it best. We need to decide which interface to build now.

## Options

| Option | Pros | Cons |
|---|---|---|
| **A. Combinational reads without valid/ready in the single-cycle core; valid/ready and synchronous reads come with the pipeline (week 2)** | The single-cycle core is as simple as possible; the memory model in cocotb is trivial; the core serves as the first reference for the Spike comparison | The interface will have to change; in this version the ports do not follow the policy |
| B. valid/ready and synchronous reads right away | One interface for the whole project | The single-cycle core needs a wait state machine: complexity in a core that the pipeline will replace anyway |

The area and frequency cost of the options were not measured.

## Decision

Option A. The `core` has the ports `imem_addr_o`, `imem_rdata_i` (32 bits), `dmem_addr_o`, `dmem_re_o`, `dmem_wdata_o`, `dmem_wstrb_o`, `dmem_rdata_i` (`XLEN` bits). Reads are combinational, writes happen on the clock edge. The memory is `XLEN` bits wide and aligned; a misaligned access is handled as a trap (`trap_o`), like an illegal instruction. The reset address is `RESET_PC = 0x8000_0000` (see the address map in the project rules).

## Consequences

- In the pipeline (week 2) the interface is replaced with valid/ready and synchronous reads, recorded in a new ADR.
- The `commit_*` ports (pc, instruction, rd, data) already exist and feed the commit log and the Spike comparison.
- Mutation check: the core tests catch faults in JALR, in branches, in writes to x0 and in traps.
- The single-cycle core showed that the register file's write-through (ADR 004) is wrong when the write and the read belong to the same instruction: `ADDI x1, x1, 1` would create a combinational loop and break the result. Therefore `regfile` got the `WRITE_THROUGH` parameter (default 1, for the pipeline), and `core` sets it to 0.
