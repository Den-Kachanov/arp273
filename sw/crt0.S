/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* Startup: set the stack, call main, report main's return value through tohost and wait */

	.section .text.init
	.globl _start
_start:
	/* The register file has no reset (ADR 005): after power-up it holds garbage, while Spike starts
	   from zeros. Clear every register, so that a program that saves an unused callee-saved register
	   behaves the same on both (and on the chip) */
	li   x1, 0
	li   x2, 0
	li   x3, 0
	li   x4, 0
	li   x5, 0
	li   x6, 0
	li   x7, 0
	li   x8, 0
	li   x9, 0
	li   x10, 0
	li   x11, 0
	li   x12, 0
	li   x13, 0
	li   x14, 0
	li   x15, 0
	li   x16, 0
	li   x17, 0
	li   x18, 0
	li   x19, 0
	li   x20, 0
	li   x21, 0
	li   x22, 0
	li   x23, 0
	li   x24, 0
	li   x25, 0
	li   x26, 0
	li   x27, 0
	li   x28, 0
	li   x29, 0
	li   x30, 0
	li   x31, 0
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
