// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov
// Verilator harness for the cores (XLEN = 64): loads a flat binary at 0x8000_0000, runs until the
// program writes to tohost or the core traps, prints a commit log in the style of
// `spike --log-commits` and the performance counters.
//
// Built twice from this file (the model class is Vtop in both, verilator --prefix Vtop):
//   default  the single-cycle core: combinational memories (ADR 008)
//   -DPIPE   the pipelined core: valid/ready ports, response one cycle or more later (ADR 013).
//            The memory is a synchronous BRAM (always ready, one cycle) unless env MEM_STALL sets
//            the probability that a port is not ready in a cycle; then the response also comes
//            0..3 cycles later at random (env MEM_SEED, default 1)
//   -DPIPE -DRELEASE  the pipelined core built with DEBUG = 0: no commit trace, so the program ends
//            when the data port accepts the store to tohost, and no commit log is written.
//            The debug pipeline build ends at the same point when env END_ON_STORE=1: then both
//            builds read the counters in the same cycle (make release-check compares them).
//            The pipelined core takes exceptions (ADR 014): by default the run still stops at the first
//            one (trap_o), as the C programs install no handler; env TRAP_STOP=0 lets the core enter its
//            handler at mtvec (riscv-tests end with ECALL, and their handler writes tohost)
// A byte stored to 0x1000_0000 (UART transmit register) is printed to stdout (CoreMark's report).
// Built with --coverage-toggle (scripts/energy.py), it writes the toggle counts to env COVERAGE_FILE.

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <vector>

#include "Vtop.h"
#include "verilated.h"

static const uint64_t BASE = 0x80000000ull;
static const uint64_t SIZE = 1ull << 20;
static std::vector<uint8_t> mem(SIZE, 0);

static bool in_ram(uint64_t addr, unsigned n)
{
	// written without addr + n: a garbage address near 2^64 would wrap around
	return addr >= BASE && addr - BASE <= SIZE - n;
}

static uint64_t read_le(uint64_t addr, unsigned n)
{
	uint64_t v = 0;
	if (!in_ram(addr, n))
		return 0;
	for (unsigned i = 0; i < n; i++)
		v |= (uint64_t)mem[addr - BASE + i] << (8 * i);
	return v;
}

// write the bytes selected by strb of an 8-byte word at base
static void write_strb(uint64_t base, uint64_t wdata, unsigned strb)
{
	for (unsigned i = 0; i < 8; i++)
		if (((strb >> i) & 1) && in_ram(base + i, 1))
			mem[base + i - BASE] = (wdata >> (8 * i)) & 0xFF;
}

// UART transmit register at 0x1000_0000 (the planned SoC address, and Spike's ns16550): a byte
// stored there goes to stdout
static const uint64_t UART_THR = 0x10000000ull;
static void uart_store(uint64_t base, uint64_t wdata, unsigned strb)
{
	if (base == UART_THR && (strb & 1)) {
		putchar((int)(wdata & 0xFF));
		fflush(stdout);
	}
}

// log a store the way Spike does: address of the first byte and the value of the stored bytes;
// return the value
static uint64_t log_store(FILE *log, uint64_t addr, uint64_t wdata, unsigned strb)
{
	unsigned first = 0, count = 0;
	while (!((strb >> first) & 1))
		first++;
	for (unsigned i = first; i < 8 && ((strb >> i) & 1); i++)
		count++;
	uint64_t value = 0;
	for (unsigned i = 0; i < count; i++)
		value |= ((wdata >> (8 * (first + i))) & 0xFF) << (8 * i);
	fprintf(log, " mem 0x%016llx 0x%0*llx", (unsigned long long)addr, (int)(2 * count),
		(unsigned long long)value);
	return value;
}

static void tick(Vtop *top)
{
	top->clk_i = 1;
	top->eval();
	top->clk_i = 0;
	top->eval();
}

#ifdef PIPE
static uint64_t rng_state = 1;
static uint64_t rng()
{
	// xorshift64: reproducible from MEM_SEED
	rng_state ^= rng_state << 13;
	rng_state ^= rng_state >> 7;
	rng_state ^= rng_state << 17;
	return rng_state;
}

struct pending_t {
	bool     busy = false;
	uint64_t due = 0;
	uint64_t data = 0;
};
#endif

int main(int argc, char **argv)
{
	if (argc < 4) {
		fprintf(stderr, "usage: %s program.bin commit.log tohost_hex [max_cycles]\n", argv[0]);
		return 2;
	}
	std::ifstream in(argv[1], std::ios::binary);
	if (!in) {
		fprintf(stderr, "cannot open %s\n", argv[1]);
		return 2;
	}
	std::vector<uint8_t> image((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
	if (image.size() > SIZE) {
		fprintf(stderr, "program does not fit in 1 MiB\n");
		return 2;
	}
	for (size_t i = 0; i < image.size(); i++)
		mem[i] = image[i];

	FILE *log = fopen(argv[2], "w");
	if (!log) {
		fprintf(stderr, "cannot open %s\n", argv[2]);
		return 2;
	}
	uint64_t tohost = strtoull(argv[3], nullptr, 16);
	uint64_t max_cycles = argc > 4 ? strtoull(argv[4], nullptr, 10) : 1000000;

	// registers without reset start with random values, as on a chip (the model is built with
	// --x-initial unique); the seed is fixed so that a run repeats; env X_SEED changes it
	Verilated::randReset(2);
	Verilated::randSeed(getenv("X_SEED") ? atoi(getenv("X_SEED")) : 1);
	Verilated::commandArgs(argc, argv);
	Vtop *top = new Vtop;

	top->clk_i = 0;
	top->rst_ni = 0;
#ifdef PIPE
	double stall = getenv("MEM_STALL") ? atof(getenv("MEM_STALL")) : 0.0;
	rng_state = getenv("MEM_SEED") ? strtoull(getenv("MEM_SEED"), nullptr, 10) | 1 : 1;
	pending_t pi, pd;
	bool trap_stop = !(getenv("TRAP_STOP") && atoi(getenv("TRAP_STOP")) == 0);
	uint64_t ntraps = 0;
#ifdef RELEASE
	bool end_on_store = true;
#else
	bool end_on_store = getenv("END_ON_STORE") && atoi(getenv("END_ON_STORE"));
#endif
	top->imem_req_ready_i = 0;
	top->imem_rsp_valid_i = 0;
	top->dmem_req_ready_i = 0;
	top->dmem_rsp_valid_i = 0;
#else
	top->imem_rdata_i = 0;
	top->dmem_rdata_i = 0;
#endif
	top->eval();
	tick(top);
	tick(top);
	top->rst_ni = 1;

	int status = 3;
	uint64_t exit_code = 0;

	for (uint64_t cycle = 0; cycle < max_cycles; cycle++) {
#ifdef PIPE
		// ready at random when MEM_STALL is set; responses that are due in this cycle
		auto ready = [&]() { return stall <= 0.0 || (double)(rng() % 1000000) / 1e6 >= stall; };
		auto latency = [&]() { return stall <= 0.0 ? 1 : 1 + (int)(rng() % 4); };
		top->imem_req_ready_i = ready();
		top->dmem_req_ready_i = ready();
		bool due_i = pi.busy && pi.due == cycle, due_d = pd.busy && pd.due == cycle;
		top->imem_rsp_valid_i = due_i;
		top->imem_rsp_data_i = due_i ? (uint32_t)pi.data : 0;
		top->dmem_rsp_valid_i = due_d;
		top->dmem_rsp_data_i = due_d ? pd.data : 0;
		top->eval();
#else
		// the memories are combinational: answer in the same cycle
		top->eval();
		top->imem_rdata_i = (uint32_t)read_le(top->imem_addr_o, 4);
		top->eval();
		top->dmem_rdata_i = read_le(top->dmem_addr_o & ~7ull, 8);
		top->eval();
#endif

#ifdef PIPE
		if (top->trap_o && !trap_stop) {
			ntraps++;
		} else
#endif
		if (top->trap_o) {
#ifdef PIPE
			uint64_t trap_pc = top->commit_pc_o;
#else
			uint64_t trap_pc = top->imem_addr_o;
#endif
			fprintf(stderr, "trap at pc 0x%016llx, instruction 0x%08x\n", (unsigned long long)trap_pc,
				top->commit_inst_o);
			status = 1;
			break;
		}

#ifdef PIPE
		// requests accepted at the coming edge; a store writes the memory when it is accepted
		if (due_i)
			pi.busy = false;
		if (due_d)
			pd.busy = false;
		if (top->imem_req_valid_o && top->imem_req_ready_i) {
			if (pi.busy) {
				fprintf(stderr, "protocol error: second instruction request while one is outstanding\n");
				status = 4;
				break;
			}
			pi = {true, cycle + latency(), read_le(top->imem_req_addr_o, 4)};
		}
		if (top->dmem_req_valid_o && top->dmem_req_ready_i) {
			if (pd.busy) {
				fprintf(stderr, "protocol error: second data request while one is outstanding\n");
				status = 4;
				break;
			}
			uint64_t addr = top->dmem_req_addr_o;
			if (top->dmem_req_we_o) {
				write_strb(addr, top->dmem_req_wdata_o, top->dmem_req_wstrb_o);
				uart_store(addr, top->dmem_req_wdata_o, top->dmem_req_wstrb_o);
				pd = {true, cycle + latency(), 0};
				if (end_on_store && addr == (tohost & ~7ull)) {
					exit_code = read_le(tohost, 8) >> 1;
					status = 0;
					break;
				}
			} else {
				pd = {true, cycle + latency(), read_le(addr, 8)};
			}
		}
#endif

		// nothing is committed in this cycle (waiting, or a bubble in the pipeline)
		if (!top->commit_valid_o) {
			tick(top);
			continue;
		}

		uint64_t pc = top->commit_pc_o;
		fprintf(log, "core   0: 3 0x%016llx (0x%08x)", (unsigned long long)pc, top->commit_inst_o);
		if (top->commit_we_o)
			fprintf(log, " x%-2d 0x%016llx", top->commit_rd_o, (unsigned long long)top->commit_data_o);

		bool done = false;
#ifdef PIPE
		if (top->commit_mem_re_o)
			fprintf(log, " mem 0x%016llx", (unsigned long long)top->commit_mem_addr_o);
		if (top->commit_mem_we_o && top->commit_mem_wstrb_o) {
			uint64_t addr = top->commit_mem_addr_o;
			uint64_t value = log_store(log, addr, top->commit_mem_wdata_o, top->commit_mem_wstrb_o);
			if (addr == tohost) {
				done = true;
				exit_code = value >> 1;
			}
		}
#else
		if (top->dmem_re_o)
			fprintf(log, " mem 0x%016llx", (unsigned long long)top->dmem_addr_o);
		if (top->dmem_wstrb_o) {
			uint64_t addr = top->dmem_addr_o;
			write_strb(addr & ~7ull, top->dmem_wdata_o, top->dmem_wstrb_o);
			uart_store(addr & ~7ull, top->dmem_wdata_o, top->dmem_wstrb_o);
			uint64_t value = log_store(log, addr, top->dmem_wdata_o, top->dmem_wstrb_o);
			if (addr == tohost) {
				done = true;
				exit_code = value >> 1;
			}
		}
#endif
		fprintf(log, "\n");
		if (done) {
			tick(top);  // let the counters see the last instruction
			status = 0;
			break;
		}
		tick(top);
	}

	fclose(log);
	uint64_t cycles = top->perf_cycles_o, instret = top->perf_instret_o;
	fprintf(stderr, "exit code %llu\n", (unsigned long long)exit_code);
	fprintf(stderr, "cycles %llu instret %llu stalls %llu flushes %llu\n", (unsigned long long)cycles,
		(unsigned long long)instret, (unsigned long long)top->perf_stalls_o,
		(unsigned long long)top->perf_flushes_o);
	if (instret)
		fprintf(stderr, "CPI %.3f\n", (double)cycles / (double)instret);
#ifdef PIPE
	if (ntraps)
		fprintf(stderr, "exceptions taken %llu\n", (unsigned long long)ntraps);
#endif
#if VM_COVERAGE
	// built with --coverage-toggle (scripts/energy.py): the toggle counts of every net, for the energy estimate
	Verilated::threadContextp()->coveragep()->write(getenv("COVERAGE_FILE") ? getenv("COVERAGE_FILE") : "coverage.dat");
#endif
	delete top;
	if (status == 3)
		fprintf(stderr, "no tohost write within %llu cycles\n", (unsigned long long)max_cycles);
	return status == 0 ? (int)exit_code : status + 100;
}
