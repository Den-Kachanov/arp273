// SPDX-License-Identifier: CERN-OHL-S-2.0
// All 13 instructions of the M extension (RV64) on edge values and random values.
// Inline assembly forces the exact instruction, so the compiler cannot replace one by another;
// the results are not checked here: the commit log is compared with Spike

#include <stdint.h>

#define BINOP(name, insn)                                                          \
	static inline uint64_t name(uint64_t a, uint64_t b)                         \
	{                                                                           \
		uint64_t r;                                                         \
		__asm__ volatile(insn " %0, %1, %2" : "=r"(r) : "r"(a), "r"(b));    \
		return r;                                                           \
	}

BINOP(op_mul, "mul")
BINOP(op_mulh, "mulh")
BINOP(op_mulhsu, "mulhsu")
BINOP(op_mulhu, "mulhu")
BINOP(op_div, "div")
BINOP(op_divu, "divu")
BINOP(op_rem, "rem")
BINOP(op_remu, "remu")
BINOP(op_mulw, "mulw")
BINOP(op_divw, "divw")
BINOP(op_divuw, "divuw")
BINOP(op_remw, "remw")
BINOP(op_remuw, "remuw")

static const uint64_t edge[] = {
	0,
	1,
	2,
	3,
	7,
	0xFFFFFFFFFFFFFFFFull,
	0xFFFFFFFFFFFFFFFEull,
	0x7FFFFFFFFFFFFFFFull,
	0x8000000000000000ull,
	0x8000000000000001ull,
	0x7FFFFFFFull,
	0x80000000ull,
	0xFFFFFFFFull,
	0xFFFFFFFF80000000ull,
	0x100000000ull,
	0x123456789ABCDEF0ull,
};

static uint64_t acc;

static void all_ops(uint64_t a, uint64_t b)
{
	acc ^= op_mul(a, b);
	acc ^= op_mulh(a, b);
	acc ^= op_mulhsu(a, b);
	acc ^= op_mulhu(a, b);
	acc ^= op_div(a, b);
	acc ^= op_divu(a, b);
	acc ^= op_rem(a, b);
	acc ^= op_remu(a, b);
	acc ^= op_mulw(a, b);
	acc ^= op_divw(a, b);
	acc ^= op_divuw(a, b);
	acc ^= op_remw(a, b);
	acc ^= op_remuw(a, b);
}

static uint64_t seed = 0x9E3779B97F4A7C15ull;

static uint64_t xorshift(void)
{
	seed ^= seed << 13;
	seed ^= seed >> 7;
	seed ^= seed << 17;
	return seed;
}

int main(void)
{
	unsigned n = sizeof(edge) / sizeof(edge[0]);

	for (unsigned i = 0; i < n; i++)
		for (unsigned j = 0; j < n; j++)
			all_ops(edge[i], edge[j]);

	// random operands; the divisor is shortened now and then so that quotients are not always 0
	for (unsigned i = 0; i < 40; i++) {
		uint64_t a = xorshift();
		uint64_t b = xorshift() >> (xorshift() & 63);
		all_ops(a, b);
	}
	return acc == 0x5555555555555555ull;    // exit code 0 unless acc has this value
}
