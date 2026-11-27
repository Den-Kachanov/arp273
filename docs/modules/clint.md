# CLINT (`rtl/soc/clint.sv`)

The machine timer and the software interrupts of every hart ([ADR 020](../adr/020-interrupts.md)). In
the SoC it sits at `0x0200_0000` ([ADR 019](../adr/019-soc-architecture.md)); the register offsets are
those of the SiFive CLINT, which Spike and QEMU also use, so the same program runs on all three.

## Registers

| Offset | Register | Width | Reset | Description |
|---|---|---|---|---|
| `0x0000 + 4 h` | `msip[h]` | 32 (bit 0) | 0 | Software interrupt of hart h: the line `msip_o[h]`. Other bits read 0 |
| `0x4000 + 8 h` | `mtimecmp[h]` | 64 | all ones | Timer interrupt of hart h: `mtip_o[h]` = `mtime >= mtimecmp[h]`, unsigned |
| `0xBFF8` | `mtime` | 64 | 0 | Counts up by one in every cycle with `tick_i` = 1; writable (a write wins over the increment) |

Any other offset reads 0 and ignores writes.

## Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `clk_i`, `rst_ni` | input | 1 | Clock, synchronous reset (active low) |
| `tick_i` | input | 1 | `mtime` increment: 1 in every cycle in simulation; on the board a fixed-rate tick, so that `mtime` does not depend on the core clock |
| `req_valid_i`, `req_ready_o` | in, out | 1 | Request handshake; always ready |
| `req_addr_i` | input | 16 | Offset in the CLINT; the interconnect decodes the base. Aligned to `XLEN/8` bytes |
| `req_we_i`, `req_wstrb_i`, `req_wdata_i` | input | 1, `XLEN/8`, `XLEN` | Write enable, byte strobes, data in its lane |
| `rsp_valid_o`, `rsp_data_o` | output | 1, `XLEN` | One response per request, one cycle later: the registers before that cycle's write; 0 for a write |
| `mtip_o`, `msip_o` | output | `NHARTS` | Interrupt lines, registered (one cycle after the register changes) |

| Parameter | Default | Meaning |
|---|---|---|
| `NHARTS` | 1 | Number of harts |

The register port is the data-port protocol of the cores ([spec](../spec.md#ports)); with the SoC bus it
gets an AXI-Lite adapter. With XLEN = 32 a 64-bit register takes two accesses. A program that writes
`mtimecmp` in two halves should first write all ones to the upper half, so that no half-written value
fires the interrupt (the usual sequence of the privileged specification).

## Testing

`tb/unit/test_clint.py`, `make unit T=clint`, XLEN = 64 and 32, `NHARTS = 2`: reset values and counting,
`tick_i`, the timer line of each hart and its delay, the unsigned 64-bit comparison, `msip` (only bit 0 is
stored), byte strobes, a write responds with 0, a write to `mtime` wins over the increment, unmapped
offsets, no write without valid. Mutation check: `make mutate M=clint`, 15 mutants, all caught. With the core: `tb/unit/test_irq.py` uses a Python model
of the same registers; `make irq` uses the model in `tb/core/main.cpp`. The RTL CLINT and the cores meet in
`soc_top` ([soc_top.md](soc_top.md)): `tb/unit/test_soc.py`, `make smp`.
