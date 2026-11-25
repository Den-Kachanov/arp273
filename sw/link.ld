/* SPDX-License-Identifier: CERN-OHL-S-2.0 */
/* SPDX-FileCopyrightText: 2026 Denys Kachanov */
/* RAM starts at 0x8000_0000, as in Spike: one ELF runs on Spike and on the core */

OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY
{
	RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 1M
}

SECTIONS
{
	.text : {
		*(.text.init)
		*(.text .text.*)
	} > RAM

	.rodata : {
		*(.rodata .rodata.*)
		*(.srodata .srodata.*)
	} > RAM

	.data : {
		*(.data .data.*)
		*(.sdata .sdata.*)
	} > RAM

	/* Spike and the core harness stop when the program writes to tohost */
	.tohost ALIGN(64) : {
		*(.tohost)
	} > RAM

	.bss : {
		*(.bss .bss.*)
		*(.sbss .sbss.*)
		*(COMMON)
	} > RAM

	. = ALIGN(16);
	_stack_top = ORIGIN(RAM) + LENGTH(RAM);
}
