/* C5VRX by Twotoz/contributors: manual private-RAM synthesis timing only.
 * No function here owns DMA or authorizes a live raw-ring modification. */
#pragma once
#include "sync_flywheel.h"

typedef enum { SFW_BENCH_H, SFW_BENCH_EQ, SFW_BENCH_BROAD, SFW_BENCH_FIRST_EQ } sfw_bench_kind_t;
typedef struct {
    uint32_t start, bytes, bootstrap_refused;
    bool prepared;
} sfw_private_pulse_t;

/* Every buffer must be caller-owned PRIVATE storage. The firmware adapter
 * allocates it independently of the live ring and verifies that separation.
 * No caller callback is accepted: prepare emits workspace bytes only. */
bool sfw_bench_prewarm(const sfw_ring_t *private_ring);
bool sfw_bench_seed_private(const sfw_ring_t *private_ring, uint16_t phase);
bool sfw_bench_prepare_private(const sfw_ring_t *private_ring, bool pal,
                              sfw_bench_kind_t kind, sfw_private_pulse_t *result);
