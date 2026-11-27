// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov
// Interrupts between harts through the CLINT (ADR 020): hart 1 sleeps in WFI with its software and timer
// interrupts enabled; hart 0 wakes it with a software interrupt (msip[1]), then sets hart 1's timer.
// Hart 1's handler counts both. Self-checking (exit code 0)

#include <stdint.h>

#define MSIP(h)     (*(volatile uint32_t *)(0x02000000ul + 4 * (h)))
#define MTIMECMP(h) (*(volatile uint64_t *)(0x02004000ul + 8 * (h)))
#define MTIME       (*(volatile uint64_t *)0x0200bff8ul)

static volatile int ready, soft, timer, stop;
static volatile uint64_t causes[4];

void __attribute__((interrupt("machine"), aligned(4))) handler(void)
{
	uint64_t cause;
	__asm__ volatile("csrr %0, mcause" : "=r"(cause));
	if (soft + timer < 4)
		causes[soft + timer] = cause;
	if (cause == ((1ul << 63) | 3)) {
		soft++;
		MSIP(1) = 0;
	} else if (cause == ((1ul << 63) | 7)) {
		timer++;
		MTIMECMP(1) = ~0ul;
	}
}

void secondary(long hart)
{
	if (hart != 1)
		return;
	MTIMECMP(1) = ~0ul;
	__asm__ volatile("csrw mtvec, %0" : : "r"(handler));
	__asm__ volatile("csrw mie, %0" : : "r"((1ul << 7) | (1ul << 3)));
	__asm__ volatile("csrsi mstatus, 8");
	ready = 1;
	while (!stop)
		__asm__ volatile("wfi");
}

int main(void)
{
	while (!ready)
		;
	MSIP(1) = 1;                         // wake hart 1
	while (!soft)
		;
	MTIMECMP(1) = MTIME + 500;           // and again, with its timer
	while (!timer)
		;
	stop = 1;
	if (soft != 1 || timer != 1)
		return 1;
	if (causes[0] != ((1ul << 63) | 3) || causes[1] != ((1ul << 63) | 7))
		return 2;
	return 0;
}
