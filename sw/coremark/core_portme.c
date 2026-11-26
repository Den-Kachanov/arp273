/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* CoreMark port: time from mcycle, seeds from volatile variables, output through ee_printf
   (sw/lib/printf.c: the UART transmit register at 0x1000_0000) */

#include "coremark.h"

#if VALIDATION_RUN
volatile ee_s32 seed1_volatile = 0x3415;
volatile ee_s32 seed2_volatile = 0x3415;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PERFORMANCE_RUN
volatile ee_s32 seed1_volatile = 0x0;
volatile ee_s32 seed2_volatile = 0x0;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PROFILE_RUN
volatile ee_s32 seed1_volatile = 0x8;
volatile ee_s32 seed2_volatile = 0x8;
volatile ee_s32 seed3_volatile = 0x8;
#endif
volatile ee_s32 seed4_volatile = ITERATIONS;
volatile ee_s32 seed5_volatile = 0;

ee_u32 default_num_contexts = 1;

static CORETIMETYPE start_time_val, stop_time_val;

static CORETIMETYPE read_mcycle(void)
{
	CORETIMETYPE t;
	__asm__ volatile("csrr %0, mcycle" : "=r"(t));
	return t;
}

void start_time(void)
{
	start_time_val = read_mcycle();
}

void stop_time(void)
{
	stop_time_val = read_mcycle();
}

CORE_TICKS get_time(void)
{
	return stop_time_val - start_time_val;
}

/* No clock frequency is assumed: "seconds" are millions of cycles, so CoreMark's
   Iterations/Sec reads as iterations per million cycles, rounded down. scripts/coremark.py
   computes CoreMark/MHz exactly from the ticks and the iterations. */
secs_ret time_in_secs(CORE_TICKS ticks)
{
	return (secs_ret)(ticks / 1000000ul);
}

void portable_init(core_portable *p, int *argc, char *argv[])
{
	(void)argc;
	(void)argv;
	if (sizeof(ee_ptr_int) != sizeof(ee_u8 *))
		ee_printf("ERROR! ee_ptr_int does not hold a pointer\n");
	if (sizeof(ee_u32) != 4)
		ee_printf("ERROR! ee_u32 is not 32 bits\n");
	p->portable_id = 1;
}

void portable_fini(core_portable *p)
{
	p->portable_id = 0;
}
