// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov
// Verilator harness for the SoC (rtl/soc/soc_top.sv, ADR 019): NHARTS cores, the CLINT and the atomics unit
// are inside the RTL; this file models what is outside: a 1 MiB RAM at 0x8000_0000 with an instruction
// port and a data port (a synchronous BRAM, or with env MEM_STALL a memory that stalls at random, as in
// tb/core/main.cpp), the UART transmit register at 0x1000_0000 and tohost.
//
// Usage: soc_sim program.bin commit.log tohost_hex [max_cycles]
//
// The run ends when the data port accepts the store to tohost (from any hart): exit code = the value >> 1.
// The commit log has the lines of all harts in the format of `spike -p<N> --log-commits` (core <h>: ...),
// harts in order within a cycle; scripts/compare.py --hart picks one. By default the run stops at the
// first exception of any hart; env TRAP_STOP=0 lets the harts take them (programs with handlers).

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

static void write_strb(uint64_t base, uint64_t wdata, unsigned strb)
{
	for (unsigned i = 0; i < 8; i++)
		if (((strb >> i) & 1) && in_ram(base + i, 1))
			mem[base + i - BASE] = (wdata >> (8 * i)) & 0xFF;
}

// n bits from bit lo of an output of any width (Verilator: plain integers up to 64 bits, VlWide above)
static uint64_t field(uint64_t v, unsigned lo, unsigned n)
{
	return n >= 64 ? v >> lo : (v >> lo) & ((1ull << n) - 1);
}

template <std::size_t W>
static uint64_t field(const VlWide<W> &v, unsigned lo, unsigned n)
{
	uint64_t r = 0;
	for (unsigned i = 0; i < n; i++) {
		unsigned b = lo + i;
		if ((v[b / 32] >> (b % 32)) & 1)
			r |= 1ull << i;
	}
	return r;
}

static uint64_t log_store(FILE *log, uint64_t addr, uint64_t wdata, unsigned strb)
{
	unsigned first = 0, count = 0;
	while (first < 8 && !((strb >> first) & 1))
		first++;
	for (unsigned i = first; i < 8 && ((strb >> i) & 1); i++)
		count++;
	uint64_t value = 0;
	for (unsigned i = 0; i < count; i++)
		value |= ((wdata >> (8 * (first + i))) & 0xFF) << (8 * i);
	fprintf(log, " mem 0x%016llx 0x%0*llx", (unsigned long long)addr, (int)(2 * count), (unsigned long long)value);
	return value;
}

static uint64_t rng_state = 1;
static uint64_t rng()
{
	rng_state ^= rng_state << 13;
	rng_state ^= rng_state >> 7;
	rng_state ^= rng_state << 17;
	return rng_state;
}

struct pending_t {
	bool busy = false;
	uint64_t due = 0;
	uint64_t data = 0;
};

static void tick(Vtop *top)
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
	std::vector<uint8_t> image((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
	if (image.empty() || image.size() > SIZE) {
		fprintf(stderr, "cannot load %s\n", argv[1]);
		return 2;
	}
	for (size_t i = 0; i < image.size(); i++)
		mem[i] = image[i];
	FILE *log = fopen(argv[2], "w");
	if (!log) {
		fprintf(stderr, "cannot write %s\n", argv[2]);
		return 2;
	}
	uint64_t tohost = strtoull(argv[3], nullptr, 16);
	uint64_t max_cycles = argc > 4 ? strtoull(argv[4], nullptr, 10) : 10000000;

	Verilated::randReset(2);
	Verilated::randSeed(getenv("X_SEED") ? atoi(getenv("X_SEED")) : 1);
	Verilated::commandArgs(argc, argv);
	Vtop *top = new Vtop;
	const unsigned H = NHARTS, X = 64, S = 8;

	double stall = getenv("MEM_STALL") ? atof(getenv("MEM_STALL")) : 0.0;
	rng_state = getenv("MEM_SEED") ? strtoull(getenv("MEM_SEED"), nullptr, 10) | 1 : 1;
	bool trap_stop = !(getenv("TRAP_STOP") && atoi(getenv("TRAP_STOP")) == 0);
	pending_t pi, pd;

	top->clk_i = 0;
	top->rst_ni = 0;
	top->tick_i = 1;
	top->irq_meip_i = 0;
	top->mem_i_req_ready_i = 0;
	top->mem_i_rsp_valid_i = 0;
	top->mem_d_req_ready_i = 0;
	top->mem_d_rsp_valid_i = 0;
	top->eval();
	tick(top);
	tick(top);
	top->rst_ni = 1;

	int status = 3;
	uint64_t exit_code = 0, ntraps = 0;
	for (uint64_t cycle = 0; cycle < max_cycles; cycle++) {
		auto ready = [&]() { return stall <= 0.0 || (double)(rng() % 1000000) / 1e6 >= stall; };
		auto latency = [&]() { return stall <= 0.0 ? 1 : 1 + (int)(rng() % 4); };
		top->mem_i_req_ready_i = ready();
		top->mem_d_req_ready_i = ready();
		bool due_i = pi.busy && pi.due == cycle, due_d = pd.busy && pd.due == cycle;
		top->mem_i_rsp_valid_i = due_i;
		top->mem_i_rsp_data_i = due_i ? (uint32_t)pi.data : 0;
		top->mem_d_rsp_valid_i = due_d;
		top->mem_d_rsp_data_i = due_d ? pd.data : 0;
		top->eval();

		for (unsigned h = 0; h < H; h++) {
			if (!field(top->trap_o, h, 1))
				continue;
			if (!trap_stop) {
				ntraps++;
				continue;
			}
			fprintf(stderr, "hart %u: trap at pc 0x%016llx, instruction 0x%08x\n", h,
				(unsigned long long)field(top->commit_pc_o, h * X, X),
				(unsigned)field(top->commit_inst_o, h * 32, 32));
			status = 1;
		}
		if (status == 1)
			break;

		if (due_i)
			pi.busy = false;
		if (due_d)
			pd.busy = false;
		if (top->mem_i_req_valid_o && top->mem_i_req_ready_i) {
			if (pi.busy) {
				fprintf(stderr, "protocol error: second instruction request while one is outstanding\n");
				status = 4;
				break;
			}
			pi = {true, cycle + latency(), read_le(top->mem_i_req_addr_o, 4)};
		}
		bool done = false;
		if (top->mem_d_req_valid_o && top->mem_d_req_ready_i) {
			if (pd.busy) {
				fprintf(stderr, "protocol error: second data request while one is outstanding\n");
				status = 4;
				break;
			}
			uint64_t addr = top->mem_d_req_addr_o;
			if (top->mem_d_req_we_o) {
				write_strb(addr, top->mem_d_req_wdata_o, top->mem_d_req_wstrb_o);
				if (addr == 0x10000000ull && (top->mem_d_req_wstrb_o & 1)) {
					putchar((int)(top->mem_d_req_wdata_o & 0xFF));
					fflush(stdout);
				}
				pd = {true, cycle + latency(), 0};
				if (addr == (tohost & ~7ull)) {
					exit_code = read_le(tohost, 8) >> 1;
					done = true;
				}
			} else {
				pd = {true, cycle + latency(), read_le(addr, 8)};
			}
		}

		// the commit log: atomics as Spike logs them (an AMO: the read and the new value in memory, which
		// the atomics unit has just written; an SC: the store if it succeeded; LR: the read)
		for (unsigned h = 0; h < H; h++) {
			if (!field(top->commit_valid_o, h, 1))
				continue;
			uint64_t pc = field(top->commit_pc_o, h * X, X);
			unsigned inst = (unsigned)field(top->commit_inst_o, h * 32, 32);
			fprintf(log, "core %3u: 3 0x%016llx (0x%08x)", h, (unsigned long long)pc, inst);
			if (field(top->commit_we_o, h, 1))
				fprintf(log, " x%-2u 0x%016llx", (unsigned)field(top->commit_rd_o, h * 5, 5),
					(unsigned long long)field(top->commit_data_o, h * X, X));
			uint64_t maddr = field(top->commit_mem_addr_o, h * X, X);
			uint64_t wdata = field(top->commit_mem_wdata_o, h * X, X);
			unsigned wstrb = (unsigned)field(top->commit_mem_wstrb_o, h * S, S);
			bool is_amo = (inst & 0x7f) == 0x2f;
			unsigned f5 = inst >> 27;
			if (is_amo && f5 != 0x02 && f5 != 0x03) {
				unsigned n = ((inst >> 12) & 7) == 3 ? 8 : 4;
				fprintf(log, " mem 0x%016llx mem 0x%016llx 0x%0*llx", (unsigned long long)maddr,
					(unsigned long long)maddr, (int)(2 * n), (unsigned long long)read_le(maddr, n));
			} else if (is_amo && f5 == 0x03) {
				if (field(top->commit_data_o, h * X, X) == 0)
					log_store(log, maddr, wdata, wstrb);
			} else {
				if (field(top->commit_mem_re_o, h, 1))
					fprintf(log, " mem 0x%016llx", (unsigned long long)maddr);
				if (field(top->commit_mem_we_o, h, 1) && wstrb)
					log_store(log, maddr, wdata, wstrb);
			}
			fprintf(log, "\n");
		}
		if (done) {
			status = 0;
			break;
		}
		tick(top);
	}

	fclose(log);
	fprintf(stderr, "exit code %llu\n", (unsigned long long)exit_code);
	for (unsigned h = 0; h < H; h++) {
		uint64_t cycles = field(top->perf_cycles_o, h * 64, 64), instret = field(top->perf_instret_o, h * 64, 64);
		fprintf(stderr, "hart %u: cycles %llu instret %llu", h, (unsigned long long)cycles, (unsigned long long)instret);
		if (instret)
			fprintf(stderr, " CPI %.3f", (double)cycles / (double)instret);
		fprintf(stderr, "\n");
	}
	if (ntraps)
		fprintf(stderr, "exceptions taken %llu\n", (unsigned long long)ntraps);
	delete top;
	if (status == 3)
		fprintf(stderr, "no tohost write within %llu cycles\n", (unsigned long long)max_cycles);
	return status == 0 ? (int)exit_code : status + 100;
}
