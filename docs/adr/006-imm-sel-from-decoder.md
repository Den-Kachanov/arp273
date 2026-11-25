# ADR 006: The decoder selects the immediate format for `imm_gen` (`imm_sel_t`)

## Context

The immediate generator needs to know the format (I, S, B, U, J). It can derive it from the opcode inside `imm_gen`, or receive it from the decoder.

## Options

| Option | Pros | Cons |
|---|---|---|
| **A. `imm_gen` receives `sel_i` of type `riscv_pkg::imm_sel_t` from the decoder** | Decoding happens in one place only; `imm_gen` is tested on its own, without opcodes | One more type in the package |
| B. `imm_gen` looks at the opcode itself | The decoder does not need to output `sel` | The same decoding in two modules |

The area of both options is about the same; there are no measurements.

## Decision

Option A. `imm_sel_t` has five values (I, S, B, U, J); the exact codes are defined by the author in `riscv_pkg.sv`.

## Consequences

- The decoder must produce the correct `sel` for every opcode; the decoder test checks this.
- Every immediate is sign-extended from bit 31 to `XLEN`; for U-type in RV64 this means extension from bit 31 (`LUI 0x80000` gives `0xFFFFFFFF80000000`).
