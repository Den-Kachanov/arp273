/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* The libm functions of the soft-float library (scripts/softfp.py, sw/softfp/libm_shim.c) */
#ifndef ARP_MATH_H
#define ARP_MATH_H
#define HUGE_VAL (__builtin_huge_val())
#define M_PI 3.14159265358979323846
double sqrt(double x);
double fabs(double x);
float fabsf(float x);
double cos(double x);
double sin(double x);
double atan(double x);
double acos(double x);
double pow(double x, double y);
double floor(double x);
double copysign(double x, double y);
double scalbn(double x, int n);
#endif
