/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* Startup: set the stack, call main, report main's return value through tohost and wait */

	.section .text.init
	.globl _start
_start:
	la   sp, _stack_top
	call main
	slli a0, a0, 1
	ori  a0, a0, 1          /* tohost = (exit code << 1) | 1 */
	la   t0, tohost
	sd   a0, 0(t0)
1:	j    1b

	.section .tohost, "aw", @progbits
	.align 6
	.globl tohost
tohost:
	.dword 0
	.globl fromhost
fromhost:
	.dword 0
