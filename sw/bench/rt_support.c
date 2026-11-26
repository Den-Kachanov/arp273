/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* Support for the riscv-tests benchmarks (third_party/riscv-tests/benchmarks) on our crt0: they call
   setStats(1) before and setStats(0) after the measured part. The counts of that part are printed in a
   line that scripts/bench.py reads; main's return value (verify(): 0 = correct) is the exit code. */

#include <stdint.h>

int printf(const char *fmt, ...);

static uint64_t cycles0, instret0;

static inline uint64_t read_mcycle(void)
{
	uint64_t v;
	__asm__ volatile("csrr %0, mcycle" : "=r"(v));
	return v;
}

static inline uint64_t read_minstret(void)
{
	uint64_t v;
	__asm__ volatile("csrr %0, minstret" : "=r"(v));
	return v;
}

void setStats(int enable)
{
	uint64_t c = read_mcycle(), i = read_minstret();
	if (enable) {
		cycles0 = c;
		instret0 = i;
	} else {
		printf("bench: measured cycles %lu instret %lu\n", (unsigned long)(c - cycles0),
		       (unsigned long)(i - instret0));
	}
}
