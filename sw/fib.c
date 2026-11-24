// SPDX-License-Identifier: CERN-OHL-S-2.0
// Fibonacci numbers in memory: loads, stores, adds and a loop branch

#define N 20

static volatile unsigned long fib[N];

int main(void)
{
	fib[0] = 0;
	fib[1] = 1;

	for (int i = 2; i < N; i++)
		fib[i] = fib[i - 1] + fib[i - 2];

	return fib[N - 1] == 4181 ? 0 : 1;
}
