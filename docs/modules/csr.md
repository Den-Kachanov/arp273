# Machine-mode CSRs (`rtl/common/csr.sv`)

The control and status registers of the pipelined core and its exception state. Only machine mode
exists. Design decisions: [ADR 014](../adr/014-csr-and-exceptions.md).

## Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `clk_i`, `rst_ni` | input | 1 | Clock, synchronous reset (active low) |
| `access_i` | input | 1 | A CSR instruction is in WB |
| `addr_i` | input | 12 | CSR address |
| `op_i` | input | 2 | `funct3[1:0]`: 01 write, 10 set bits, 11 clear bits |
| `write_i` | input | 1 | 0 for CSRRS/CSRRC with rs1 = x0 (or uimm = 0): read only |
| `wdata_i` | input | `XLEN` | rs1, or the zero-extended immediate |
| `rdata_o` | output | `XLEN` | The old value (goes to rd) |
| `illegal_o` | output | 1 | The CSR does not exist, or a write to a read-only CSR |
| `trap_i`, `cause_i`, `epc_i`, `tval_i` | input | 1, `XLEN` × 3 | Take an exception |
| `mret_i` | input | 1 | MRET commits |
| `mtvec_o`, `mepc_o` | output | `XLEN` | Redirect targets |
| `cycle_i`, `instret_i` | input | 64 | The performance counters (ADR 013) |
| `irq_i` | input | 3 | Interrupt lines {MEIP, MTIP, MSIP}, levels; registered here ([ADR 020](../adr/020-interrupts.md)) |
| `irq_pending_o` | output | 1 | A registered line is set with its `mie` bit: wakes WFI (whatever MIE is) |
| `irq_take_o`, `irq_code_o` | output | 1, 4 | Also `mstatus.MIE` is set: the core takes the interrupt with this code, MEI (11) > MSI (3) > MTI (7) |

All outputs are combinational from the address and the registers; the registers change at the clock edge.
`trap_i` has priority over `mret_i`, and both over a CSR write (they never come together from the core).

## CSRs

| Address | CSR | Behaviour |
|---|---|---|
| 0x300 | `mstatus` | MIE (bit 3) and MPIE (bit 7) writable; MPP (12:11) reads 11, only M-mode; other bits 0 |
| 0x301 | `misa` | Reads MXL (2 for XLEN = 64, 1 for 32) and the A, I and M bits; writes ignored (WARL) |
| 0x304 | `mie` | MSIE, MTIE, MEIE writable |
| 0x305 | `mtvec` | Direct mode only: bits 1:0 read 0 |
| 0x340 | `mscratch` | Read/write |
| 0x341 | `mepc` | Bits 1:0 read 0 (no C extension) |
| 0x342 | `mcause` | Read/write; the exception code |
| 0x343 | `mtval` | Read/write; the bad address or instruction |
| 0x344 | `mip` | MSIP, MTIP, MEIP: the registered interrupt lines; writes ignored |
| 0xB00, 0xB02 | `mcycle`, `minstret` | Read the counters; writes ignored (simplification, ADR 014) |
| 0xC00, 0xC02 | `cycle`, `instret` | Read-only copies |
| 0xB80, 0xB82, 0xC80, 0xC82 | high halves | XLEN = 32 only |
| 0xF11–0xF15 | `mvendorid`, `marchid`, `mimpid`, `mhartid`, `mconfigptr` | Read 0, except `mhartid`: the parameter `HART_ID` |

Every other address is an illegal instruction (`satp`, `pmp*`, `medeleg`, `mideleg`, `stvec`...). The
startup code of `riscv-tests` relies on this: it points `mtvec` past each optional CSR write first.

## Exceptions

| Cause | Code | `mtval` | Found in |
|---|---|---|---|
| Instruction address misaligned (jump or taken branch) | 0 | target | EX |
| Illegal instruction | 2 | instruction bits | ID (decoder) or WB (CSR) |
| Breakpoint (EBREAK) | 3 | pc | EX |
| Load address misaligned | 4 | address | MEM |
| Store/AMO address misaligned | 6 | address | MEM |
| ECALL from M-mode | 11 | 0 | EX |

On an exception: `mepc` = pc of the instruction, `mcause`, `mtval`, `MPIE` = `MIE`, `MIE` = 0. On MRET:
`MIE` = `MPIE`, `MPIE` = 1. An interrupt takes the same path with `mcause` = interrupt bit + code and
`mtval` = 0 ([ADR 020](../adr/020-interrupts.md)).

## Testing

- `tb/unit/test_csr.py` runs on the pipelined core, XLEN = 64 and 32 (see ADR 014).
- `tb/unit/test_atomic.py` (`misa`, `mhartid`) and `tb/unit/test_irq.py` (`mip`, priority, masking) also check it.
- `make mutate M=csr`: 25 mutants, 24 caught, 1 equivalent.
- `make riscv-tests`: `rv64ui-p`, `rv64um-p`, `rv64ua-p`, `rv64mi-p` (14 of 17; see [ADR 020](../adr/020-interrupts.md)).

## Status

Area measured 2026-11-25, no commit hash (estimate), `make area`:

| XLEN | LUT6 | flip-flops |
|---|---|---|
| 64 | 746 | 323 |
| 32 | 415 | 163 |
