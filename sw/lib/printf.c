/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* A small printf for benchmark reports: %d %i %u %x %X %s %c %% with the l modifier, a field width and
   zero padding. Characters go to the UART transmit register at 0x1000_0000 (the core harness prints
   them; Spike has an ns16550 there). printf, debug_printf (riscv-tests) and ee_printf (CoreMark) are
   the same function. */

#include <stdarg.h>

#define UART_THR ((volatile unsigned char *)0x10000000ul)

static int put(char c)
{
	*UART_THR = (unsigned char)c;
	return 1;
}

static int put_num(unsigned long v, unsigned base, int upper, int neg, int width, char pad)
{
	char buf[24];
	const char *digits = upper ? "0123456789ABCDEF" : "0123456789abcdef";
	int n = 0, out = 0;
	do {
		buf[n++] = digits[v % base];
		v /= base;
	} while (v);
	if (neg)
		width--;
	if (neg && pad == '0')
		out += put('-');
	for (; width > n; width--)
		out += put(pad);
	if (neg && pad != '0')
		out += put('-');
	while (n)
		out += put(buf[--n]);
	return out;
}

int vprintf(const char *fmt, va_list ap)
{
	int out = 0;
	for (; *fmt; fmt++) {
		if (*fmt != '%') {
			out += put(*fmt);
			continue;
		}
		fmt++;
		char pad = ' ';
		if (*fmt == '0') {
			pad = '0';
			fmt++;
		}
		int width = 0;
		while (*fmt >= '0' && *fmt <= '9')
			width = width * 10 + (*fmt++ - '0');
		int lng = 0;
		while (*fmt == 'l') {
			lng = 1;
			fmt++;
		}
		switch (*fmt) {
		case 'd':
		case 'i': {
			long v = lng ? va_arg(ap, long) : va_arg(ap, int);
			out += put_num(v < 0 ? -(unsigned long)v : (unsigned long)v, 10, 0, v < 0, width, pad);
			break;
		}
		case 'u':
			out += put_num(lng ? va_arg(ap, unsigned long) : va_arg(ap, unsigned), 10, 0, 0, width, pad);
			break;
		case 'x':
		case 'X':
			out += put_num(lng ? va_arg(ap, unsigned long) : va_arg(ap, unsigned), 16, *fmt == 'X', 0,
				       width, pad);
			break;
		case 's':
			for (const char *s = va_arg(ap, const char *); *s; s++)
				out += put(*s);
			break;
		case 'c':
			out += put((char)va_arg(ap, int));
			break;
		case '\0':
			fmt--;
			break;
		default:
			out += put(*fmt);
			break;
		}
	}
	return out;
}

int printf(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	int n = vprintf(fmt, ap);
	va_end(ap);
	return n;
}

/* weak: a program may define its own (Dhrystone has a debug_printf that prints nothing) */
int ee_printf(const char *fmt, ...) __attribute__((weak, alias("printf")));

__attribute__((weak)) void debug_printf(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	vprintf(fmt, ap);
	va_end(ap);
}
