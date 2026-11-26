/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* assert: a failed check ends the program with exit code 134 (as SIGABRT would) */
#ifndef ARP_ASSERT_H
#define ARP_ASSERT_H
void exit(int code) __attribute__((noreturn));
#ifdef NDEBUG
#define assert(e) ((void)0)
#else
#define assert(e) ((e) ? (void)0 : exit(134))
#endif
#endif
