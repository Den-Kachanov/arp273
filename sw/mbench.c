// SPDX-License-Identifier: CERN-OHL-S-2.0
// SPDX-FileCopyrightText: 2026 Denys Kachanov
// M extension on ordinary numbers (not the worst case of muldiv.c): an int matrix product (MULW),
// 64-bit hashing with a large constant (MUL with a full 64-bit multiplier), decimal conversion and
// Euclid's algorithm (DIVU, REMU on moderate values), signed division of small ints (DIVW, REMW).
// Inputs come through volatile variables so that the compiler cannot fold or strength-reduce them

#define N 6
#define EXPECTED 0x2e69edd3a5600a41ul  // the same source built for the host (gcc x86-64) and run there

static volatile int vseed = 7;
static volatile unsigned long vbase = 10;
static volatile unsigned long vgold = 0x9E3779B97F4A7C15ul;
static volatile int vdiv = -3;

static int ma[N][N], mb[N][N], mc[N][N];

int main(void)
{
	unsigned long acc = 0;

	// int matrix product: MULW
	int s = vseed;
	for (int i = 0; i < N; i++)
		for (int j = 0; j < N; j++) {
			s = s * 1103515245 + 12345;
			ma[i][j] = (s >> 16) % 100 - 50;
			s = s * 1103515245 + 12345;
			mb[i][j] = (s >> 16) % 100 - 50;
		}
	for (int i = 0; i < N; i++)
		for (int j = 0; j < N; j++) {
			int t = 0;
			for (int k = 0; k < N; k++)
				t += ma[i][k] * mb[k][j];
			mc[i][j] = t;
			acc += (unsigned int)t;
		}

	// 64-bit hashing with a large odd constant: MUL, full-width multiplier
	unsigned long h = 0, gold = vgold;
	for (int i = 0; i < 16; i++)
		h = (h ^ (unsigned long)mc[i % N][(i * 5) % N]) * gold;
	acc ^= h;

	// decimal digits of moderate numbers: DIVU, REMU
	unsigned long base = vbase;
	for (unsigned long v = 123456789ul; v > 0; v /= base)
		acc += v % base;

	// Euclid: REMU
	unsigned long x = 1071ul * 1000003ul, y = 462ul * 1000003ul;
	while (y) {
		unsigned long t = x % y;
		x = y;
		y = t;
	}
	acc += x;

	// small signed ints: DIVW, REMW
	int d = vdiv;
	for (int i = -20; i <= 20; i += 3)
		acc += (unsigned int)(i / d) + (unsigned int)(i % d);

	return acc == EXPECTED ? 0 : 1;
}
