# Performance counters (`rtl/core/perf_counters.sv`)

Four 64-bit counters of events since reset: cycles (`cycles_o`), retired instructions (`instret_o`), stall cycles (`stalls_o`) and pipeline flushes (`flushes_o`). The inputs `commit_i`, `stall_i`, `flush_i` are the events of the current cycle. Synchronous reset `rst_ni`. The width does not depend on `XLEN`.

In the single-cycle core, `stall_i = 1` in the cycles spent waiting for `muldiv` (`XLEN / 2 + 1` or `XLEN + 1` cycles per M instruction depending on the operands, ADR 011; 0 without M instructions), and `flush_i` is 1 on JAL, JALR and a taken branch (the events that would flush instructions in a pipeline). In `test_core.py`, for a program with M instructions, `cycles = instret + stalls` holds up to the trap cycle. In the pipeline (week 2) the inputs will be connected to the real signals.

Tests: `tb/unit/test_perf_counters.py`: every counter counts its own event, reset in the middle of operation, 64-bit width. Separately, `test_core.py::test_perf_counters` checks the counters on a loop with a known number of instructions and taken branches. Mutation check: `make mutate M=perf_counters`, 8 mutants, all caught.
