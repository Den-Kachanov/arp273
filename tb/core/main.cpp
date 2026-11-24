// SPDX-License-Identifier: CERN-OHL-S-2.0
// Verilator harness for the single-cycle core (XLEN = 64): loads a flat binary at 0x8000_0000,
// runs until the program writes to tohost or the core traps, prints a commit log in the style of
// `spike --log-commits` and the performance counters

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <vector>

#include "Vcore.h"
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

static void tick(Vcore *top)
{
	top->clk_i = 1;
	top->eval();
	top->clk_i = 0;
	top->eval();
}

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

	Verilated::commandArgs(argc, argv);
	Vcore *top = new Vcore;

	top->clk_i = 0;
	top->rst_ni = 0;
	top->imem_rdata_i = 0;
	top->dmem_rdata_i = 0;
	top->eval();
	tick(top);
	tick(top);
	top->rst_ni = 1;

	int status = 3;
	uint64_t exit_code = 0;

	for (uint64_t cycle = 0; cycle < max_cycles; cycle++) {
		// the memories are combinational: answer in the same cycle
		top->eval();
		top->imem_rdata_i = (uint32_t)read_le(top->imem_addr_o, 4);
		top->eval();
		top->dmem_rdata_i = read_le(top->dmem_addr_o & ~7ull, 8);
		top->eval();

		if (top->trap_o) {
			fprintf(stderr, "trap at pc 0x%016llx, instruction 0x%08x\n",
				(unsigned long long)top->commit_pc_o, top->commit_inst_o);
			status = 1;
			break;
		}

		// the core waits for the multiplier/divider: nothing is committed in this cycle
		if (!top->commit_valid_o) {
			tick(top);
			continue;
		}

		uint64_t pc = top->commit_pc_o;
		fprintf(log, "core   0: 3 0x%016llx (0x%08x)", (unsigned long long)pc, top->commit_inst_o);
		if (top->commit_we_o)
			fprintf(log, " x%-2d 0x%016llx", top->commit_rd_o, (unsigned long long)top->commit_data_o);

		if (top->dmem_re_o)
			fprintf(log, " mem 0x%016llx", (unsigned long long)top->dmem_addr_o);

		bool done = false;
		unsigned strb = top->dmem_wstrb_o;
		if (strb) {
			unsigned first = 0, count = 0;
			while (!((strb >> first) & 1))
				first++;
			for (unsigned i = first; i < 8 && ((strb >> i) & 1); i++)
				count++;
			uint64_t addr = top->dmem_addr_o;
			uint64_t base = addr & ~7ull;
			uint64_t value = 0;
			for (unsigned i = 0; i < count; i++) {
				uint8_t b = (top->dmem_wdata_o >> (8 * (first + i))) & 0xFF;
				value |= (uint64_t)b << (8 * i);
				if (in_ram(base + first + i, 1))
					mem[base + first + i - BASE] = b;
			}
			fprintf(log, " mem 0x%016llx 0x%0*llx", (unsigned long long)addr, (int)(2 * count),
				(unsigned long long)value);
			if (addr == tohost) {
				done = true;
				exit_code = value >> 1;
			}
		}
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
	delete top;
	if (status == 3)
		fprintf(stderr, "no tohost write within %llu cycles\n", (unsigned long long)max_cycles);
	return status == 0 ? (int)exit_code : status + 100;
}
