/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* Glue for the soft-float library of the benchmarks (scripts/softfp.py): the public names of the
   libm functions they call, on top of the IEEE cores of fdlibm (newlib), without errno handling (a
   bare-metal program has no errno to read), plus fabs and the overflow helpers that newlib's
   scalbn and sqrt return through. */

#include <stdint.h>

double __ieee754_pow(double, double);
double __ieee754_sqrt(double);
double __ieee754_acos(double);

double pow(double x, double y)
{
	return __ieee754_pow(x, y);
}

double sqrt(double x)
{
	return __ieee754_sqrt(x);
}

double acos(double x)
{
	return __ieee754_acos(x);
}

double fabs(double x)
{
	union {
		double d;
		uint64_t u;
	} v = {x};
	v.u &= ~(1ull << 63);
	return v.d;
}

float fabsf(float x)
{
	union {
		float f;
		uint32_t u;
	} v = {x};
	v.u &= ~(1u << 31);
	return v.f;
}

/* +-infinity and +-0 with the overflow or underflow flag raised, as in newlib's math_err.c */
double __math_oflow(uint32_t sign)
{
	volatile double h = 0x1p769;
	return (sign ? -h : h) * h;
}

double __math_uflow(uint32_t sign)
{
	volatile double t = 0x1p-767;
	return (sign ? -t : t) * t;
}
