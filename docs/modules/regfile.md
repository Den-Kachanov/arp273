# Register file (`rtl/common/regfile.sv`)

Diagram: [img/regfile.svg](../img/regfile.svg). Connections between modules and area: [overview.md](overview.md).

The RV64I register file: 32 registers of `XLEN` bits, one write port and two read ports. Reads are combinational, writes happen on the rising clock edge.

Decisions: [ADR 004](../adr/004-regfile-write-through.md) (write-through), [ADR 005](../adr/005-regfile-no-reset-no-x0-storage.md) (no reset, x0 not stored).

## Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `clk_i` | input | 1 | Clock; write on the rising edge |
| `we_i` | input | 1 | Write enable |
| `waddr_i` | input | 5 | Write address |
| `wdata_i` | input | `XLEN` | Write data |
| `raddr1_i`, `raddr2_i` | input | 5 | Read addresses |
| `rdata1_o`, `rdata2_o` | output | `XLEN` | Read data, combinational |

## Parameter

`WRITE_THROUGH` (default 1): 1 enables step 2 of the read priority (pipeline); 0 disables it, so a read of the address being written returns the old value (single-cycle core, ADR 004 and 008).

## Behaviour

- Write: on the `clk_i` edge if `we_i = 1` and `waddr_i != 0`. Writes to x0 are ignored.
- Each read port, in priority order: address 0 gives 0; otherwise, if `we_i = 1` and `waddr_i` equals the read address, `wdata_i` is returned (write-through); otherwise the value from the array `mem[1:31]`.
- There is no reset. A register has an undefined value until its first write. Tests and programs must not read a register that was never written; `crt0` clears x1–x31 (ADR 012).

## Testing

File `tb/unit/test_regfile.py`, run with `make unit T=regfile`, XLEN = 64 and 32. Compared against a Python model. Eight tests: initial values, write and read of all x1..x31, independent ports, x0 protection, write-through, x0 priority over write-through, write disabled (`we_i = 0`), a random sequence of operations (seed 1).

Mutation check: `make mutate M=regfile`, 12 mutants, 11 caught, 1 equivalent (allowing a write to x0: the array is declared `[1:31]`, so the write falls outside it, and reads of x0 are forced to 0).

## Status

`make lint` is clean for XLEN = 64 and 32, `make unit` passes, Yosys synthesizes the module (`make synth`); area in [overview.md](overview.md). Fmax not measured.
