/* C5VRX by Twotoz/contributors: adversarial streaming admission tests. */
#include <assert.h>
#include <stdio.h>
#include "sync_stream_guard.h"

static sync_stream_guard_t base(void)
{
    return (sync_stream_guard_t){
        .rx_complete = 65536, .rx_upper = 69628, .tx_upper = 53696,
        .protected_begin = 61444, .ring_bytes = 32768, .rate_hz = 40000000,
        .snapshot_age_us = 5, .operation_bound_us = 10,
        .rx_backlog_bytes = 128, .rx_burst_proven = true,
        .context_verified = true, .prefetch_proven = true, .source_original = true
    };
}

static uint32_t rng = 0x397c912bu;
static uint32_t random_u(void) { rng = rng * 1664525u + 1013904223u; return rng; }

/* Independently evaluate accepted intervals with 128-bit integer positions.
 * Model worst DMA burst/advance, without reusing the helper's checked uint64
 * operations. The source, context and lease flags are part of the contract. */
static void independent_interval_oracle(void)
{
    unsigned accepted = 0;
    for (unsigned i = 0; i < 60000u; ++i) {
        sync_stream_guard_t g = base();
        const uint32_t sizes[] = {128u, 4096u, 32768u};
        g.ring_bytes = sizes[random_u() % 3u];
        uint64_t origin = (uint64_t)(random_u() % 10000u) * g.ring_bytes;
        g.rx_complete = origin + 4u * g.ring_bytes;
        g.rx_upper = g.rx_complete + 1u + random_u() % (g.ring_bytes - 1u);
        g.tx_upper = g.rx_complete - random_u() % g.ring_bytes;
        g.protected_begin = g.rx_complete - random_u() % g.ring_bytes;
        g.snapshot_age_us = random_u() % 80u;
        g.operation_bound_us = 1u + random_u() % 20u;
        g.rx_backlog_bytes = random_u() % 512u;
        uint64_t a = g.tx_upper + random_u() % g.ring_bytes;
        uint64_t b = a + 1u + random_u() % 1280u;
        uint64_t source_a = g.rx_complete - random_u() % g.ring_bytes;
        uint64_t source_b = source_a + 1u + random_u() % 100u;
        if (sync_stream_admit(&g, a, b, source_a, source_b) != SSG_OK) continue;
        ++accepted;
        __uint128_t ticks = ((__uint128_t)g.snapshot_age_us + g.operation_bound_us) * g.rate_hz;
        __uint128_t advance = ticks / 1000000u + (ticks % 1000000u != 0);
        __uint128_t first = a - a % 64u;
        __uint128_t last = (__uint128_t)b + ((64u - b % 64u) % 64u);
        __uint128_t dma_tx = (__uint128_t)g.tx_upper + advance;
        __uint128_t dma_rx = (__uint128_t)g.rx_upper + advance + g.rx_backlog_bytes;
        assert(first >= dma_tx);
        assert(first + g.ring_bytes >= dma_rx);
        assert((__uint128_t)source_a + g.ring_bytes >= dma_rx);
        assert(last <= g.protected_begin && source_b <= g.rx_complete);
        assert(g.source_original && !g.raw_reader_active && g.context_verified);
    }
    assert(accepted > 100u);
    printf("60000 adversarial intervals: %u accepted whole cache ranges independently safe\n", accepted);
}

int main(void)
{
    sync_stream_guard_t g = base();
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_OK);
    g.prefetch_proven = false;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_CONTEXT);
    g = base(); g.rx_burst_proven = false;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_CONTEXT);
    g = base(); g.raw_reader_active = true;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_OBSERVER);
    g = base(); g.source_original = false;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_SOURCE);
    g = base();
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 65537) == SSG_SOURCE);
    /* Exact stores can appear safe while their cache-line flush is late. */
    assert(sync_stream_admit(&g, 54301, 54537, 54400, 54540) == SSG_TX_DEADLINE);
    assert(sync_stream_admit(&g, 61210, 61440, 61150, 61442) == SSG_OK);
    assert(sync_stream_admit(&g, 61210, 61445, 61150, 61450) == SSG_PROTECTED);
    /* 32-byte tail: its actual end changes RX's upper bound by 32, not4092. */
    g.rx_upper = g.rx_complete + 32;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_OK);
    g = base(); g.snapshot_age_us = 820;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_AGE);
    g = base(); g.snapshot_age_us = UINT32_MAX; g.operation_bound_us = UINT32_MAX;
    g.rate_hz = UINT32_MAX;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_AGE);
    g = base(); g.operation_bound_us = 200;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_TX_DEADLINE);
    g = base(); g.tx_upper = g.rx_complete + 1;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_FRONTIERS);
    g = base(); g.rx_upper = g.rx_complete;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_FRONTIERS);
    g = base(); g.tx_upper = 40000; g.rx_upper = g.rx_complete + 32760;
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_RX_OVERWRITE);
    g = base(); g.protected_begin = 57346; /* optional8190snapshot */
    assert(sync_stream_admit(&g, 58000, 58236, 57900, 58280) == SSG_PROTECTED);
    g = base(); g.tx_upper -= 32768; /* unknown TX lap */
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_FRONTIERS);
    /* Same relative coordinates shifted by many complete rings stay valid. */
    g = base();
    const uint64_t shift = UINT64_C(32768) * 1000000u;
    g.rx_complete += shift; g.rx_upper += shift;
    g.tx_upper += shift; g.protected_begin += shift;
    assert(sync_stream_admit(&g, 55000 + shift, 55236 + shift,
                             54900 + shift, 55280 + shift) == SSG_OK);
    /* A short-tail descriptor can drain queued FIFO data faster than sample
     * rate. Synthetic backlog=512 is a fixture, not a proven hardware size. */
    g = base(); g.rx_upper = 65568; g.tx_upper = 33000;
    g.snapshot_age_us = 0; g.operation_bound_us = 1; g.rx_backlog_bytes = 0;
    assert(sync_stream_admit(&g, 33280, 33380, 33280, 33400) == SSG_OK);
    g.rx_backlog_bytes = 512;
    assert(sync_stream_admit(&g, 33280, 33380, 33280, 33400) == SSG_RX_OVERWRITE);
    assert(sync_stream_admit(&g, 55000, 55236, 33280, 33400) == SSG_RX_OVERWRITE);
    /* Input overflow/near-wrap is refused before any rounded cache stores. */
    g = base();
    assert(sync_stream_admit(&g, UINT64_MAX - 100, UINT64_MAX - 5, 54900, 55280) == SSG_SOURCE);
    g = base();
    const uint64_t near_end = UINT64_MAX - 69630;
    g.rx_complete += near_end; g.rx_upper += near_end;
    g.tx_upper += near_end; g.protected_begin += near_end;
    assert(sync_stream_admit(&g, 55000 + near_end, 55236 + near_end,
                             54900 + near_end, 55280 + near_end) == SSG_RX_OVERWRITE);
    independent_interval_oracle();
    puts("PASS: context, provenance, cache-line deadlines, tail, lap, observer and RX-overwrite guards");
}
