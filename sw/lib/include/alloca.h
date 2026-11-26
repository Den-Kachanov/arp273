/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* alloca for programs built without a C library */
#ifndef ARP_ALLOCA_H
#define ARP_ALLOCA_H
#define alloca(n) __builtin_alloca(n)
#endif
