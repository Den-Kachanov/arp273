// SPDX-License-Identifier: CERN-OHL-S-2.0
// Arithmetic, logic, shifts and comparisons on 64-bit and 32-bit values, with branches on the results

static volatile unsigned long out_u[8];
static volatile long          out_s[8];
static volatile unsigned int  out_w[8];

int main(void)
{
	unsigned long x = 0x123456789ABCDEFul;
	unsigned int y = 0x9E3779B9u;
	long s = -1;
	long less = 0, below = 0;

	for (int i = 0; i < 24; i++) {
		x ^= x << 13;                 // sll, xor
		x ^= x >> 7;                  // srl
		x ^= x << 17;
		y ^= y << 5;                  // sllw
		y ^= y >> 3;                  // srlw
		s = (s >> 1) - (long)i;       // sra, sub
		if ((long)x < s)              // slt
			less++;
		if (x < (unsigned long)s)     // sltu
			below++;
		if (i == 11) {
			out_u[0] = x;
			out_w[0] = y;
		}
	}

	out_u[1] = x;
	out_u[2] = x & 0xF0F0F0F0F0F0F0F0ul;
	out_u[3] = x | 0x0F0F0F0F0F0F0F0Ful;
	out_s[0] = s;
	out_s[1] = less;
	out_s[2] = below;
	out_s[3] = (int)y;                    // addiw / sext.w
	out_w[1] = y;
	out_w[2] = y + 0x7FFFFFFFu;           // addw wraps
	out_w[3] = (unsigned int)((int)y >> 4); // sraw

	return 0;
}
