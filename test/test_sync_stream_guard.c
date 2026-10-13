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
        .context_verified = true, .prefetch_proven = true, .source_original = true
    };
}

int main(void)
{
    sync_stream_guard_t g = base();
    assert(sync_stream_admit(&g, 55000, 55236, 54900, 55280) == SSG_OK);
    g.prefetch_proven = false;
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
    puts("PASS: context, provenance, cache-line deadlines, tail, lap, observer and RX-overwrite guards");
}
