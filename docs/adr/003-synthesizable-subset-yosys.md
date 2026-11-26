# ADR 003: The SystemVerilog subset that Yosys understands

## Context

The project rules require a Yosys synthesis every week, before a board is available. Lint and simulation run through Verilator, which accepts almost all of SystemVerilog, so code can be green in Verilator and fail in Yosys.

While preparing the author's guide, I tested the built-in parser of Yosys 0.66 (`read_verilog -sv`) on short examples. Result:

| Construct | Yosys 0.66 |
|---|---|
| `import pkg::*;` in a module header or body | not supported |
| Qualified names `pkg::name` and `pkg::type` | works |
| `return` in a function | not supported; assign the result to the function name |
| `assert ... else ...` | not supported |
| `typedef enum`, `packed struct`, `always_comb`, `always_ff`, `generate if`, `$clog2` | work in the examples used (see `rtl/common/alu.sv`; the examples themselves are in the author's guide, kept outside the repository) |

This was checked only on those examples. I did not compile a full list of the constructs Yosys 0.66 supports, so other constructs (interfaces, arrays of structs, `unique case`) must be checked when they are needed.

## Options

| Option | Pros | Cons |
|---|---|---|
| **A. Write in the subset that Yosys understands** (qualified `riscv_pkg::...` names, no `import` or `return`, asserts only under `` `ifndef SYNTHESIS ``) | Nothing extra in the tool flow; synthesis and lint use the same files | Slightly more text (qualified names); every new construct risks hitting a limitation |
| B. The yosys-slang plugin | Full SystemVerilog support in Yosys | Another dependency that has to be built; an extra CI step |
| C. The sv2v converter before Yosys | Simple flow: SV to Verilog, then Yosys | Another stage and another tool; error messages point at generated code |
| D. No weekly synthesis, wait for the board | Less work now | Breaks the "synthesis every week" policy; problems are found late |

## Decision

Option A as the starting point. Rules for files in `rtl/`:

1. Do not write `import pkg::*;`. Use qualified names: `riscv_pkg::XLEN`, `riscv_pkg::alu_op_t`, `riscv_pkg::ALU_ADD`.
2. Do not write `return` in functions: assign the result to the function name.
3. Wrap assertions with `else` in `` `ifndef SYNTHESIS `` ... `` `endif ``.
4. `make synth` runs every module through Yosys. If lint is green but `make synth` fails, check this list first.

Revisit: if a needed construct does not pass Yosys and the workaround hurts readability, move to option B or C. That is a new ADR.

## Consequences

- All current code (`riscv_pkg.sv`, `alu.sv`) already follows these rules and synthesizes.
- Verilator lint does not check these rules; only `make synth` does. So `make synth` belongs in CI next to `make lint`.
- Every new module in `rtl/` must pass `make synth` before it counts as done.
- If a SystemVerilog interface or another construct outside the tested set is needed, the risk shows up in synthesis, not in simulation. It is better to test such a construct on a small example before building a module on it.
