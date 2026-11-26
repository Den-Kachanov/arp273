/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* The few C library functions the benchmarks call (the programs are built with -nostdlib). Simple byte
   loops on purpose: they are part of what is measured, the same on every core. */

#include <stddef.h>

void *memset(void *d, int c, size_t n)
{
	unsigned char *p = d;
	while (n--)
		*p++ = (unsigned char)c;
	return d;
}

void *memcpy(void *d, const void *s, size_t n)
{
	unsigned char *p = d;
	const unsigned char *q = s;
	while (n--)
		*p++ = *q++;
	return d;
}

size_t strlen(const char *s)
{
	size_t n = 0;
	while (s[n])
		n++;
	return n;
}

char *strcpy(char *d, const char *s)
{
	char *p = d;
	while ((*p++ = *s++))
		;
	return d;
}

int strcmp(const char *a, const char *b)
{
	while (*a && *a == *b) {
		a++;
		b++;
	}
	return (unsigned char)*a - (unsigned char)*b;
}

int memcmp(const void *a, const void *b, size_t n)
{
	const unsigned char *p = a, *q = b;
	for (; n--; p++, q++)
		if (*p != *q)
			return *p - *q;
	return 0;
}

void *memmove(void *d, const void *s, size_t n)
{
	unsigned char *p = d;
	const unsigned char *q = s;
	if (p < q) {
		while (n--)
			*p++ = *q++;
	} else {
		while (n--)
			p[n] = q[n];
	}
	return d;
}

char *strchr(const char *s, int c)
{
	for (;; s++) {
		if (*s == (char)c)
			return (char *)s;
		if (!*s)
			return NULL;
	}
}

/* exit from anywhere: the same report through tohost as a return from main (sw/crt0.S) */
extern volatile unsigned long tohost;

void exit(int code)
{
	tohost = ((unsigned long)code << 1) | 1;
	for (;;)
		;
}

/* The character class table that the ctype.h macros of the toolchain's headers (newlib) read: entry 0
   is EOF, then one entry per character. ASCII only. Flag values as in newlib's ctype.h */
#define CT_U 01
#define CT_L 02
#define CT_N 04
#define CT_S 010
#define CT_P 020
#define CT_C 040
#define CT_X 0100
#define CT_B 0200
const char _ctype_[1 + 256] = {
	[1 + 0 ... 1 + 31] = CT_C,
	[1 + '\t' ... 1 + '\r'] = CT_C | CT_S,
	[1 + ' '] = CT_S | CT_B,
	[1 + '!' ... 1 + '/'] = CT_P,
	[1 + '0' ... 1 + '9'] = CT_N,
	[1 + ':' ... 1 + '@'] = CT_P,
	[1 + 'A' ... 1 + 'F'] = CT_U | CT_X,
	[1 + 'G' ... 1 + 'Z'] = CT_U,
	[1 + '[' ... 1 + '`'] = CT_P,
	[1 + 'a' ... 1 + 'f'] = CT_L | CT_X,
	[1 + 'g' ... 1 + 'z'] = CT_L,
	[1 + '{' ... 1 + '~'] = CT_P,
	[1 + 127] = CT_C,
};

/* a bump allocator over a static heap: the benchmarks allocate a few blocks and never free them */
static unsigned char heap[16384] __attribute__((aligned(16)));
static size_t heap_used;

void *malloc(size_t n)
{
	n = (n + 15) & ~(size_t)15;
	if (heap_used + n > sizeof heap)
		return NULL;
	void *p = &heap[heap_used];
	heap_used += n;
	return p;
}

void free(void *p)
{
	(void)p;
}
