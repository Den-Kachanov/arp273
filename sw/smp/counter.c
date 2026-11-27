// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov
// Every hart increments shared counters at the same time, three ways: an AMO (amoadd), compare-and-swap
// (an LR/SC loop) and a plain increment inside a spinlock (amoswap). If any of them lost an update the
// totals would be short. Hart 0 waits for the others and checks; self-checking (exit code 0)

#ifndef HARTS
#define HARTS 2
#endif
#define N 300

static volatile long counter, cas_counter, plain;
static volatile int lock;
static volatile int done[HARTS];

static void work(void)
{
	for (int i = 0; i < N; i++) {
		__atomic_fetch_add(&counter, 1, __ATOMIC_RELAXED);

		long old = cas_counter;
		while (!__atomic_compare_exchange_n(&cas_counter, &old, old + 1, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST))
			;

		while (__atomic_exchange_n(&lock, 1, __ATOMIC_ACQUIRE))
			;
		plain = plain + 1;
		__atomic_store_n(&lock, 0, __ATOMIC_RELEASE);
	}
}

void secondary(long hart)
{
	work();
	__atomic_store_n(&done[hart], 1, __ATOMIC_RELEASE);
}

int main(void)
{
	work();
	for (int h = 1; h < HARTS; h++)
		while (!__atomic_load_n(&done[h], __ATOMIC_ACQUIRE))
			;
	if (counter != (long)N * HARTS)
		return 1;
	if (cas_counter != (long)N * HARTS)
		return 2;
	if (plain != (long)N * HARTS)
		return 3;
	return 0;
}
