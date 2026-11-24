// SPDX-License-Identifier: CERN-OHL-S-2.0
// Loads and stores of every size and signedness: byte lanes, strobes, sign and zero extension

static volatile signed char    sb_arr[16];
static volatile unsigned char  ub_arr[16];
static volatile short          sh_arr[8];
static volatile unsigned short uh_arr[8];
static volatile int            sw_arr[4];
static volatile unsigned int   uw_arr[4];
static volatile long           sd_arr[2];

static volatile long result[8];

int main(void)
{
	long sum_b = 0, sum_ub = 0, sum_h = 0, sum_uh = 0, sum_w = 0, sum_uw = 0;

	for (int i = 0; i < 16; i++) {
		sb_arr[i] = (signed char)(0x78 + i);   // crosses the sign boundary at i = 8
		ub_arr[i] = (unsigned char)(0xF8 + i); // wraps around
	}
	for (int i = 0; i < 8; i++) {
		sh_arr[i] = (short)(0x7FFC + i);
		uh_arr[i] = (unsigned short)(0xFFFC + i);
	}
	for (int i = 0; i < 4; i++) {
		sw_arr[i] = (int)(0x7FFFFFFE + i);
		uw_arr[i] = 0xFFFFFFFEu + i;
	}
	sd_arr[0] = (long)0x8000000000000000ul;
	sd_arr[1] = 0x7FFFFFFFFFFFFFFFl;

	for (int i = 0; i < 16; i++) {
		sum_b += sb_arr[i];
		sum_ub += ub_arr[i];
	}
	for (int i = 0; i < 8; i++) {
		sum_h += sh_arr[i];
		sum_uh += uh_arr[i];
	}
	for (int i = 0; i < 4; i++) {
		sum_w += sw_arr[i];
		sum_uw += uw_arr[i];
	}

	result[0] = sum_b;
	result[1] = sum_ub;
	result[2] = sum_h;
	result[3] = sum_uh;
	result[4] = sum_w;
	result[5] = sum_uw;
	result[6] = sd_arr[0] + sd_arr[1];
	result[7] = (long)result[0] ^ (long)result[5];

	return 0;
}
