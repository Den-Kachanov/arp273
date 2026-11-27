// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov
// race-free: every hart retires the same instructions whatever the timing, so make smp compares each
// hart with Spike. Each hart hashes its own data; hart 1 then wakes hart 0 with a software interrupt,
// and hart 0 waits for it in WFI (with MIE clear: WFI just ends), not in a loop whose length would
// depend on timing. Hart 0 checks both results (exit code 0)

#include <stdint.h>

#define MSIP(h) (*(volatile uint32_t *)(0x02000000ul + 4 * (h)))

static volatile uint64_t result[2];
static uint64_t data[2][64];

static uint64_t work(int hart)
{
	uint64_t x = 0x9e3779b97f4a7c15ul * (uint64_t)(hart + 1), h = 0;
	for (int i = 0; i < 64; i++)
		data[hart][i] = x = x * 6364136223846793005ul + 1442695040888963407ul;
	for (int i = 0; i < 64; i++)
		h = (h ^ data[hart][(i * 5) & 63]) * 1099511628211ul;
	return h;
}

void secondary(long hart)
{
	if (hart != 1)
		return;
	result[1] = work(1);
	__atomic_thread_fence(__ATOMIC_RELEASE);
	MSIP(0) = 1;
}

int main(void)
{
	__asm__ volatile("csrw mie, %0" : : "r"(1ul << 3));   // MSIE; MIE stays clear
	result[0] = work(0);
	__asm__ volatile("wfi");                               // until hart 1's msip
	MSIP(0) = 0;
	__atomic_thread_fence(__ATOMIC_ACQUIRE);
	if (result[0] == result[1] || result[0] == 0 || result[1] == 0)
		return 1;
	return 0;
}
