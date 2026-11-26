// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov
// The A extension as a compiler uses it: fetch-and-op builtins (AMOs), compare-and-swap (an LR/SC
// loop), a spinlock, and AMO min/max from inline assembly; .W and .D, positive and negative values.
// Only for the pipelined core: the single-cycle reference core stops on atomics (ADR 019)

static volatile int  w[4] = {5, -7, 100, 0};
static volatile long d[2] = {1, -(1L << 40)};
static volatile int  lock;
static volatile long result[12];

static long amomin_d(volatile long *p, long v)
{
	long old;
	__asm__ volatile("amomin.d %0, %2, (%1)" : "=r"(old) : "r"(p), "r"(v) : "memory");
	return old;
}

static int amomaxu_w(volatile int *p, int v)
{
	int old;
	__asm__ volatile("amomaxu.w %0, %2, (%1)" : "=r"(old) : "r"(p), "r"(v) : "memory");
	return old;
}

int main(void)
{
	long sum = 0;

	for (int i = 0; i < 8; i++) {
		sum += __atomic_fetch_add(&w[0], i, __ATOMIC_SEQ_CST);        // amoadd.w
		sum += __atomic_fetch_xor(&w[1], 0x55 << i, __ATOMIC_RELAXED); // amoxor.w
		sum += __atomic_fetch_and(&d[0], ~(1L << i), __ATOMIC_ACQUIRE); // amoand.d
		sum += __atomic_fetch_or(&d[1], 1L << (2 * i), __ATOMIC_RELEASE); // amoor.d
	}
	result[0] = sum;
	result[1] = __atomic_exchange_n(&w[2], -1, __ATOMIC_ACQ_REL);   // amoswap.w
	result[2] = amomin_d(&d[1], -(1L << 50));
	result[3] = amomaxu_w(&w[3], -3);                               // unsigned: 0xFFFFFFFD wins

	// compare-and-swap: an LR/SC loop; one that succeeds and one that fails
	int expected = w[0];
	result[4] = __atomic_compare_exchange_n(&w[0], &expected, 1234, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
	long dexp = 42;
	result[5] = __atomic_compare_exchange_n(&d[0], &dexp, 99, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
	result[6] = dexp;

	// a spinlock around a critical section
	for (int i = 0; i < 4; i++) {
		while (__atomic_exchange_n(&lock, 1, __ATOMIC_ACQUIRE))
			;
		result[7] += i;
		__atomic_store_n(&lock, 0, __ATOMIC_RELEASE);
	}

	result[8] = w[0] + w[1] + w[2] + w[3];
	result[9] = d[0] ^ d[1];
	// every value is checked by the comparison with Spike (register writes and stores); exit code 0
	return 0;
}
