# Branch condition unit (`rtl/common/branch_unit.sv`)

Diagram: [img/branch_unit.svg](../img/branch_unit.svg). Connections between modules and area: [overview.md](overview.md).

A combinational unit: it compares `rs1` and `rs2` according to `funct3` and tells whether the branch condition holds. The branch target is computed by the ALU, so the comparison needs its own comparators. The `branch_o` signal from the decoder is applied outside: the unit itself does not know it.

| Port | Direction | Width | Description |
|---|---|---|---|
| `a_i`, `b_i` | input | `XLEN` | rs1, rs2 |
| `funct3_i` | input | 3 | 000 BEQ, 001 BNE, 100 BLT, 101 BGE, 110 BLTU, 111 BGEU |
| `taken_o` | output | 1 | 1 if the condition holds; always 0 for 010 and 011 |

Tests: `tb/unit/test_branch_unit.py`, `make unit T=branch_unit`: edge and random values (including equal ones), signed against unsigned, reserved `funct3` values. Mutation check: `make mutate M=branch_unit`, 8 mutants, all caught. Lint is clean for XLEN = 64 and 32, Yosys synthesizes the module.
