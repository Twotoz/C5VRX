/* SPDX-License-Identifier: GPL-3.0-only */

/* Fail the link before any RF build can place static application state in the
 * fixed vendor dump window. Runtime heap reservation is emitted by
 * c5vrx_rf_dump_producer.c through SOC_RESERVE_MEMORY_REGION(). */
ASSERT(_bss_end <= 0x40830000,
       "C5VRX: static DRAM overlaps fixed RF dump RAM 0x40830000..0x4083ffff")
