// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov
// Interrupts from the CLINT (0x0200_0000, ADR 019): a periodic timer and a software interrupt, handled
// by a C function while the main program computes. Self-checking (exit code 0): when the interrupts
// arrive depends on the clock, so the commit log cannot be compared with Spike's; the result can.
// Runs on the pipelined core (make irq) and on Spike

#include <stdint.h>

#define CLINT_MSIP     (*(volatile uint32_t *)0x02000000ul)
#define CLINT_MTIMECMP (*(volatile uint64_t *)0x02004000ul)
#define CLINT_MTIME    (*(volatile uint64_t *)0x0200bff8ul)

#define PERIOD 2000
#define TICKS  5

static volatile int ticks, soft;
static volatile uint64_t mcause_seen[8];

void __attribute__((interrupt("machine"), aligned(4))) handler(void)
{
	uint64_t cause;
	__asm__ volatile("csrr %0, mcause" : "=r"(cause));
	if (soft + ticks < 8)
		mcause_seen[soft + ticks] = cause;
	if (cause == ((1ul << 63) | 7)) {          // timer: next period, or stop
		ticks++;
		CLINT_MTIMECMP = ticks < TICKS ? CLINT_MTIME + PERIOD : ~0ul;
	} else if (cause == ((1ul << 63) | 3)) {   // software
		soft++;
		CLINT_MSIP = 0;
	}
}

static uint64_t work(uint64_t x)
{
	// something to interrupt: a hash with multiplications and memory traffic
	static uint64_t buf[64];
	for (int i = 0; i < 64; i++)
		buf[i] = x = x * 6364136223846793005ul + 1442695040888963407ul;
	uint64_t h = 0;
	for (int i = 0; i < 64; i++)
		h ^= buf[(i * 7) & 63] >> (i & 31);
	return h;
}

int main(void)
{
	__asm__ volatile("csrw mtvec, %0" : : "r"(handler));
	CLINT_MTIMECMP = CLINT_MTIME + PERIOD;
	__asm__ volatile("csrw mie, %0" : : "r"((1ul << 7) | (1ul << 3)));   // MTIE, MSIE
	__asm__ volatile("csrsi mstatus, 8");                                 // MIE

	CLINT_MSIP = 1;                            // a software interrupt to ourselves, taken at once

	uint64_t h = 1, reference = 1;
	int rounds = 0;
	while (ticks < TICKS) {
		h = work(h);
		rounds++;
		if (rounds > 5000)
			return 2;                          // the timer never fired
	}
	__asm__ volatile("csrci mstatus, 8");

	// the same computation without interrupts must give the same value
	for (int i = 0; i < rounds; i++)
		reference = work(reference);
	if (h != reference)
		return 3;
	if (soft != 1)
		return 4;
	for (int i = 0; i < TICKS + 1; i++)
		if (!(mcause_seen[i] >> 63))
			return 5;

	// WFI with MIE clear wakes on a pending timer interrupt and goes on
	CLINT_MTIMECMP = CLINT_MTIME + 100;
	__asm__ volatile("wfi");
	if (CLINT_MTIME < CLINT_MTIMECMP)
		return 6;
	return 0;
}
