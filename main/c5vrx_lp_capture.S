/* SPDX-License-Identifier: GPL-3.0-only */

/*
 * Bounded ESP32-C5 FE/IQ dump kernel.
 *
 * The RF dump engine temporarily owns HP-SRAM banks that may contain normal
 * FreeRTOS stacks or cached code.  Everything between the HP_SRAM_USAGE handoff
 * and restore therefore executes from LP RAM, uses an LP-RAM stack and makes no
 * calls.  The register sequence is the mode-0/software-trigger subset recovered
 * from ESP-IDF v6.0.2 librftest.a:adctrig().
 *
 * uint32_t c5vrx_lp_capture_mode0(int32_t sample_count_minus_one)
 * Returns the final DUMP_CTRL value; bit 18 means the vendor done flag was seen.
 */

    .section .rtc.text,"ax",@progbits
    .align  2
    .global c5vrx_lp_capture_mode0
    .type   c5vrx_lp_capture_mode0, @function
c5vrx_lp_capture_mode0:
    la      t1, c5vrx_lp_capture_stage
    li      t2, 1
    sw      t2, 0(t1)                /* entered LP kernel */
    mv      t0, sp
    la      sp, c5vrx_lp_capture_stack_top
    addi    sp, sp, -32
    sw      t0, 16(sp)               /* original FreeRTOS stack */
    sw      ra, 20(sp)
    sw      a0, 24(sp)               /* sample_count_minus_one */

    /* MODEM_CLOCK = UINT32_MAX. */
    li      t0, 0x600a9c04
    li      t1, -1
    sw      t1, 0(t0)

    /* FE_ENABLE |= 4. */
    li      t0, 0x600a0800
    lw      t1, 0(t0)
    ori     t1, t1, 4
    sw      t1, 0(t0)

    /* DUMP_CTRL length=(N-1)+1 and mode bit 17 clear. */
    li      t0, 0x600a9004
    lw      t1, 0(t0)
    li      t2, 0xfffe0000
    and     t1, t1, t2
    lw      t2, 24(sp)
    addi    t2, t2, 1
    li      t3, 0x0001ffff
    and     t2, t2, t3
    or      t1, t1, t2
    li      t2, 0xfffdffff
    and     t1, t1, t2
    sw      t1, 0(t0)

    /* Exact mode-0 DUMP_FORMAT fields, followed by bit 24 enable. */
    li      t0, 0x600a9018
    lw      t1, 0(t0)
    li      t2, 0xff03ffff
    and     t1, t1, t2
    li      t2, 0x006c0000
    or      t1, t1, t2
    sw      t1, 0(t0)
    lw      t1, 0(t0)
    li      t2, 0xfffc0fff
    and     t1, t1, t2
    li      t2, 0x0001a000
    or      t1, t1, t2
    sw      t1, 0(t0)
    lw      t1, 0(t0)
    li      t2, 0xfffff03f
    and     t1, t1, t2
    ori     t1, t1, 0x640
    sw      t1, 0(t0)
    lw      t1, 0(t0)
    andi    t1, t1, -64
    ori     t1, t1, 0x18
    li      t2, 0x01000000
    or      t1, t1, t2
    sw      t1, 0(t0)

    /* Save ownership state in LP RAM, then hand both observed banks to MAC in
     * one store. No normal-memory access is allowed after this point. */
    la      t1, c5vrx_lp_capture_stage
    li      t2, 2
    sw      t2, 0(t1)                /* configuration complete */
    li      t0, 0x60095004
    lw      t1, 0(t0)
    sw      t1, 0(sp)
    li      t2, 0xfffef0ff
    and     t1, t1, t2
    li      t2, 0x00010200
    or      t1, t1, t2
    sw      t1, 0(t0)
    fence   iorw, iorw
    la      t1, c5vrx_lp_capture_stage
    li      t2, 3
    sw      t2, 0(t1)                /* MAC owns dump SRAM */

    /* DUMP_CTRL bits 20..21 and mode bit 17 clear. */
    li      t0, 0x600a9004
    lw      t1, 0(t0)
    li      t2, 0xffcfffff
    and     t1, t1, t2
    li      t2, 0xfffdffff
    and     t1, t1, t2
    sw      t1, 0(t0)

    /* Mode 0: ordinary receive path and recovered pointer/mode selector. */
    li      t0, 0x600a20b4
    lw      t1, 0(t0)
    andi    t1, t1, -2
    sw      t1, 0(t0)
    li      t0, 0x600a9008
    lw      t1, 0(t0)
    li      t2, 0x01e00000
    or      t1, t1, t2
    sw      t1, 0(t0)

    /* Enable and pulse software trigger. */
    li      t0, 0x600a9004
    lw      t1, 0(t0)
    li      t2, 0x80000000
    or      t1, t1, t2
    sw      t1, 0(t0)
    lw      t1, 0(t0)
    li      t2, 0x00080000
    or      t1, t1, t2
    sw      t1, 0(t0)
    not     t2, t2
    and     t1, t1, t2
    sw      t1, 0(t0)
    fence   iorw, iorw
    la      t1, c5vrx_lp_capture_stage
    li      t2, 4
    sw      t2, 0(t1)                /* producer triggered */

    /* Do not depend on the vendor modem timer while modem SRAM ownership is
     * changing. mcycle is CPU-local and keeps this poll bounded to 50 ms at
     * the configured 240 MHz CPU clock, still well below the 300 ms interrupt
     * watchdog. Hardware showed that the former 10 ms deadline always expired
     * before bit 18 and returned the same stale SRAM block repeatedly. */
    csrr    t4, mcycle
    li      t5, 12000000
1:
    lw      t6, 0(t0)
    li      t1, 0x00040000
    and     t1, t6, t1
    bnez    t1, 2f
    csrr    t1, mcycle
    sub     t1, t1, t4
    bltu    t1, t5, 1b

2:
    la      t1, c5vrx_lp_capture_stage
    li      t2, 5
    sw      t2, 0(t1)                /* done bit or bounded timeout */
    /* Disable producer, restore CPU ownership atomically, then return. */
    li      t1, 0x80000000
    not     t1, t1
    and     t2, t6, t1
    sw      t2, 0(t0)
    li      t0, 0x60095004
    lw      t1, 0(sp)
    sw      t1, 0(t0)
    fence   iorw, iorw
    la      t1, c5vrx_lp_capture_stage
    li      t2, 6
    sw      t2, 0(t1)                /* CPU ownership restored */

    mv      a0, t6
    lw      ra, 20(sp)
    lw      t0, 16(sp)
    mv      sp, t0
    la      t1, c5vrx_lp_capture_stage
    li      t2, 7
    sw      t2, 0(t1)                /* original task stack restored */
    ret
    .size   c5vrx_lp_capture_mode0, .-c5vrx_lp_capture_mode0

/*
 * uint32_t c5vrx_lp_direct_av_probe_mode0(uint32_t duration_cycles,
 *                                         uint32_t lead_words)
 *
 * All normal driver work is complete before entry. PARLIO/GDMA/BitScrambler
 * are loop-armed but the PARLIO TX clock at PCR+0xb4 bit 18 is paused. This
 * LP-RAM routine grants dump ownership, starts the exact mode-0 producer,
 * waits for a writer lead, enables the clock for a bounded observation, then
 * disables both engines and restores CPU ownership before touching HP RAM.
 */
    .section .rtc.text,"ax",@progbits
    .align  2
    .global c5vrx_lp_direct_av_probe_mode0
    .type   c5vrx_lp_direct_av_probe_mode0, @function
c5vrx_lp_direct_av_probe_mode0:
    mv      t0, sp
    la      sp, c5vrx_lp_capture_stack_top
    addi    sp, sp, -48
    sw      t0, 16(sp)
    sw      ra, 20(sp)
    sw      a0, 24(sp)               /* bounded run cycles */
    sw      a1, 28(sp)               /* requested lead words */
    sw      zero, 32(sp)
    sw      zero, 36(sp)
    sw      zero, 40(sp)

    la      t0, c5vrx_lp_direct_writer_advance
    sw      zero, 0(t0)
    la      t0, c5vrx_lp_direct_pointer_changes
    sw      zero, 0(t0)
    la      t0, c5vrx_lp_direct_pointer_wraps
    sw      zero, 0(t0)
    la      t0, c5vrx_lp_direct_lead_acquired
    sw      zero, 0(t0)

    /* Save and grant the vendor-observed two-bank/64-KiB MAC dump window. */
    li      t0, 0x60095004
    lw      t1, 0(t0)
    sw      t1, 0(sp)
    li      t2, 0xfffef0ff
    and     t1, t1, t2
    li      t2, 0x00010200
    or      t1, t1, t2
    sw      t1, 0(t0)
    fence   iorw, iorw

    /* Exact mode-0 enable and software-trigger pulse. */
    li      t0, 0x600a9004
    lw      t1, 0(t0)
    li      t2, 0x80000000
    or      t1, t1, t2
    sw      t1, 0(t0)
    li      t2, 0x00080000
    or      t1, t1, t2
    sw      t1, 0(t0)
    not     t2, t2
    and     t1, t1, t2
    sw      t1, 0(t0)
    fence   iorw, iorw

    /* Tight pointer tracking establishes an actual half-ring lead. Give up
     * after 50 ms at 240 MHz; in that case PARLIO is never enabled. */
    li      t3, 0x600a9008
    lw      t4, 0(t3)
    li      t5, 0x3fff
    and     t4, t4, t5              /* previous pointer */
    sw      zero, 32(sp)            /* accumulated lead */
    csrr    t5, mcycle
1:
    lw      t6, 0(t3)
    li      a2, 0x3fff
    and     t6, t6, a2              /* current pointer */
    sub     a2, t6, t4
    li      a3, 0x3fff
    and     a2, a2, a3              /* modulo advance */
    beqz    a2, 2f
    lw      a3, 32(sp)
    add     a3, a3, a2
    sw      a3, 32(sp)
    mv      t4, t6
2:
    lw      a3, 32(sp)
    lw      a2, 28(sp)
    bgeu    a3, a2, 3f
    csrr    a2, mcycle
    sub     a2, a2, t5
    li      a3, 12000000
    bltu    a2, a3, 1b
    j       7f

3:
    la      a2, c5vrx_lp_direct_lead_acquired
    li      a3, 1
    sw      a3, 0(a2)
    /* PCR_PARL_CLK_TX_CONF_REG, documented TX clock enable bit 18. */
    li      a2, 0x600960b4
    lw      a3, 0(a2)
    li      a4, 0x00040000
    or      a3, a3, a4
    sw      a3, 0(a2)
    fence   iorw, iorw

    sw      zero, 32(sp)            /* measured writer advance */
    sw      zero, 36(sp)            /* pointer changes */
    sw      zero, 40(sp)            /* wraps */
    csrr    t5, mcycle
4:
    lw      t6, 0(t3)
    li      a2, 0x3fff
    and     t6, t6, a2
    sub     a2, t6, t4
    li      a3, 0x3fff
    and     a2, a2, a3
    beqz    a2, 6f
    lw      a3, 32(sp)
    add     a3, a3, a2
    sw      a3, 32(sp)
    lw      a3, 36(sp)
    addi    a3, a3, 1
    sw      a3, 36(sp)
    bgeu    t6, t4, 5f
    lw      a3, 40(sp)
    addi    a3, a3, 1
    sw      a3, 40(sp)
5:
    mv      t4, t6
6:
    csrr    a2, mcycle
    sub     a2, a2, t5
    lw      a3, 24(sp)
    bltu    a2, a3, 4b

7:
    /* Stop PARLIO consumption before removing MAC ownership. */
    li      a2, 0x600960b4
    lw      a3, 0(a2)
    li      a4, 0xfffbffff
    and     a3, a3, a4
    sw      a3, 0(a2)
    li      a2, 0x80000000
    not     a2, a2
    lw      t1, 0(t0)
    and     t1, t1, a2
    sw      t1, 0(t0)
    fence   iorw, iorw

    li      a2, 0x60095004
    lw      a3, 0(sp)
    sw      a3, 0(a2)
    fence   iorw, iorw

    la      a2, c5vrx_lp_direct_writer_advance
    lw      a3, 32(sp)
    sw      a3, 0(a2)
    la      a2, c5vrx_lp_direct_pointer_changes
    lw      a3, 36(sp)
    sw      a3, 0(a2)
    la      a2, c5vrx_lp_direct_pointer_wraps
    lw      a3, 40(sp)
    sw      a3, 0(a2)
    la      a2, c5vrx_lp_direct_last_pointer
    sw      t4, 0(a2)

    mv      a0, t1
    lw      ra, 20(sp)
    lw      t0, 16(sp)
    mv      sp, t0
    ret
    .size c5vrx_lp_direct_av_probe_mode0, .-c5vrx_lp_direct_av_probe_mode0

    .section .rtc_noinit,"aw",@nobits
    .balign 16
    .global c5vrx_lp_capture_stage
c5vrx_lp_capture_stage:
    .word   0
    .global c5vrx_lp_direct_writer_advance
c5vrx_lp_direct_writer_advance:
    .word   0
    .global c5vrx_lp_direct_pointer_changes
c5vrx_lp_direct_pointer_changes:
    .word   0
    .global c5vrx_lp_direct_pointer_wraps
c5vrx_lp_direct_pointer_wraps:
    .word   0
    .global c5vrx_lp_direct_lead_acquired
c5vrx_lp_direct_lead_acquired:
    .word   0
    .global c5vrx_lp_direct_last_pointer
c5vrx_lp_direct_last_pointer:
    .word   0
    .balign 16
c5vrx_lp_capture_stack:
    .space  2048
c5vrx_lp_capture_stack_top:
