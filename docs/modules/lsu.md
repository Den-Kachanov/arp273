# Load/store alignment (`rtl/common/lsu.sv`)

Diagram: [img/lsu.svg](../img/lsu.svg). Connections between modules and area: [overview.md](overview.md).

A combinational unit between the datapath and an `XLEN`-bit-wide memory: for a store it shifts the data into its byte lane and produces byte strobes; for a load it extracts the lane and sign- or zero-extends it. Misaligned accesses are not supported: the unit only raises `misaligned_o` (misaligned access is the first feature to be cut in the project plan).

| Port | Direction | Width | Description |
|---|---|---|---|
| `funct3_i` | input | 3 | Size and signedness: `[1:0]` 0 byte, 1 halfword, 2 word, 3 doubleword (RV64); `[2]` unsigned |
| `addr_lo_i` | input | 3 | Low address bits; for XLEN = 32 `[1:0]` is used |
| `store_data_i` | input | `XLEN` | rs2 data for a store |
| `wdata_o` | output | `XLEN` | Data shifted into its lane |
| `wstrb_o` | output | `XLEN/8` | Byte write strobes |
| `rdata_i` | input | `XLEN` | Word from memory |
| `load_data_o` | output | `XLEN` | Load result after shift and extension |
| `misaligned_o` | output | 1 | The address offset is not a multiple of the size |

Tests: `tb/unit/test_lsu.py`, `make unit T=lsu`: every size, offset and signedness (aligned only), exact byte strobes, the misalignment flag, independence of the load result from `store_data_i`. Mutation check: `make mutate M=lsu`, 11 mutants (halfword sign, unsigned flag ignored, strobe not shifted, word alignment checked on bit 0 only and others), all caught. Lint is clean for XLEN = 64 and 32, Yosys synthesizes the module.
