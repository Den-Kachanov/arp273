/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* Character classes from the table _ctype_ in sw/lib/libc.c (entry 0 is EOF; ASCII only) */
#ifndef ARP_CTYPE_H
#define ARP_CTYPE_H
extern const char _ctype_[1 + 256];
#define CT_U 01
#define CT_L 02
#define CT_N 04
#define CT_S 010
#define CT_P 020
#define CT_C 040
#define CT_X 0100
#define CT_B 0200
#define _ctype_of(c) (_ctype_[1 + (unsigned char)(c)])
#define isalpha(c)  (_ctype_of(c) & (CT_U | CT_L))
#define isupper(c)  ((_ctype_of(c) & (CT_U | CT_L)) == CT_U)
#define islower(c)  ((_ctype_of(c) & (CT_U | CT_L)) == CT_L)
#define isdigit(c)  (_ctype_of(c) & CT_N)
#define isxdigit(c) (_ctype_of(c) & (CT_X | CT_N))
#define isspace(c)  (_ctype_of(c) & CT_S)
#define ispunct(c)  (_ctype_of(c) & CT_P)
#define isalnum(c)  (_ctype_of(c) & (CT_U | CT_L | CT_N))
#define isprint(c)  (_ctype_of(c) & (CT_P | CT_U | CT_L | CT_N | CT_B))
#define iscntrl(c)  (_ctype_of(c) & CT_C)
static inline int toupper(int c) { return islower(c) ? c - 'a' + 'A' : c; }
static inline int tolower(int c) { return isupper(c) ? c - 'A' + 'a' : c; }
#endif
