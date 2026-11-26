/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* Embench configuration for the arp273 cores. CPU_MHZ = 1: each benchmark runs its reference number of
   iterations (Embench scales the work by the clock); the cycle count is what is compared */
#ifndef CONFIG_H
#define CONFIG_H
#define CPU_MHZ     1
#define WARMUP_HEAT 1
#endif
