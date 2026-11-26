/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* printf of sw/lib/printf.c (output to the UART) */
#ifndef ARP_STDIO_H
#define ARP_STDIO_H
#include <stdarg.h>
#include <stddef.h>
int printf(const char *fmt, ...);
int vprintf(const char *fmt, va_list ap);
#endif
