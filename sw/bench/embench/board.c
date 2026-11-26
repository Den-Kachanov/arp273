/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* Embench board support: start_trigger and stop_trigger read mcycle and minstret around the measured
   part and print the counts for scripts/bench.py; main's return value (0 = verified) is the exit code */

#include <stdint.h>

int printf(const char *fmt, ...);

static uint64_t cycles0, instret0;

#define CSR_READ(name, v) __asm__ volatile("csrr %0, " #name : "=r"(v))

void initialise_board(void)
{
}

void start_trigger(void)
{
	CSR_READ(mcycle, cycles0);
	CSR_READ(minstret, instret0);
}

void stop_trigger(void)
{
	uint64_t c, i;
	CSR_READ(mcycle, c);
	CSR_READ(minstret, i);
	printf("bench: measured cycles %lu instret %lu\n", (unsigned long)(c - cycles0),
	       (unsigned long)(i - instret0));
}
