/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* The few stdlib functions of sw/lib/libc.c */
#ifndef ARP_STDLIB_H
#define ARP_STDLIB_H
#include <stddef.h>
void *malloc(size_t n);
void free(void *p);
void exit(int code) __attribute__((noreturn));
void abort(void) __attribute__((noreturn));
static inline int abs(int x) { return x < 0 ? -x : x; }
#endif
