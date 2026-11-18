/* C5VRX by Twotoz and the C5VRX contributors: channel link frame parser.
 *
 * Hardware experiments with an external FPGA (hardware_experiments/
 * tang9k_dual) set the receiver channel over one UART wire that the FPGA
 * drives to every C5 (broadcast, 115200 8N1). A frame is four bytes:
 *   0xC5, 'C', index, 0x79 ^ index      (index = band * 8 + channel, rf.h)
 * The FPGA repeats it continuously, so a C5 that rebooted or missed a frame
 * follows within one repetition; the C5 retunes only when the index differs
 * from its own (console.c, control.c). Pure logic, host-tested
 * (test/test_channel_link.c). */
#pragma once
#include <stdint.h>

#define CHANNEL_LINK_SYNC 0xC5u
#define CHANNEL_LINK_TAG  0x43u   /* 'C' */
#define CHANNEL_LINK_KEY  0x79u   /* CHANNEL_LINK_SYNC ^ CHANNEL_LINK_TAG ^ 0xFF */

typedef struct { uint8_t state, index; } channel_link_t;

/* Feed one byte; returns the channel index of a complete valid frame whose
 * index is below `channels`, else -1. Resynchronises on any error. */
static inline int channel_link_feed(channel_link_t *p, uint8_t b, unsigned channels)
{
    switch (p->state) {
    case 0: p->state = b == CHANNEL_LINK_SYNC ? 1u : 0u; return -1;
    case 1: p->state = b == CHANNEL_LINK_TAG ? 2u : (b == CHANNEL_LINK_SYNC ? 1u : 0u); return -1;
    case 2: p->index = b; p->state = 3u; return -1;
    default:
        p->state = b == CHANNEL_LINK_SYNC ? 1u : 0u;
        if (b != (uint8_t)(CHANNEL_LINK_KEY ^ p->index) || p->index >= channels) return -1;
        p->state = 0u;
        return p->index;
    }
}
