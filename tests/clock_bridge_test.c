/* SPDX-License-Identifier: GPL-3.0-only */

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "c5vrx_clock_bridge.h"

static double tone_rms(const uint8_t *buf, size_t count, double *dc_out)
{
    double sum = 0.0, dc = 0.0;
    for (size_t i = 0; i < count; ++i) dc += buf[i];
    dc /= (double)count;
    for (size_t i = 0; i < count; ++i) {
        const double v = (double)buf[i] - dc;
        sum += v * v;
    }
    if (dc_out) *dc_out = dc;
    return sqrt(sum / (double)count);
}

int main(void)
{
    static uint8_t storage[4096];
    c5vrx_clock_bridge_t b;
    c5vrx_clock_bridge_config_t cfg = {
        .storage = storage, .capacity = sizeof(storage),
        .input_rate = 1000000u, .output_rate = 1000000u,
        .method = C5VRX_BRIDGE_LINEAR,
    };

    /* Invalid configs must be refused. */
    assert(!c5vrx_clock_bridge_init(&b, &(c5vrx_clock_bridge_config_t){0}));
    assert(!c5vrx_clock_bridge_init(&b, &(c5vrx_clock_bridge_config_t){
        .storage = storage, .capacity = 3, .input_rate = 1,
        .output_rate = 1}));

    /* ---- Ratio 1:1 linear: identity passthrough. */
    assert(c5vrx_clock_bridge_init(&b, &cfg));
    uint8_t in[64], out[64];
    for (unsigned i = 0; i < 64u; ++i) in[i] = (uint8_t)(i & 63u);
    c5vrx_clock_bridge_push(&b, in, 64u, false);
    size_t got = 0, total = 0;
    while ((got = c5vrx_clock_bridge_pull(&b, out + total,
                                          64u - total)) != 0u) {
        total += got;
    }
    assert(total >= 60u);
    for (size_t i = 1; i < total; ++i) assert(out[i] == in[i]);
    printf("identity: produced=%zu\n", total);

    /* ---- Slight ratio error: bounded occupancy, counted accounting. */
    cfg.input_rate = 1001u;
    cfg.output_rate = 1000u;
    assert(c5vrx_clock_bridge_init(&b, &cfg));
    unsigned seed = 99u;
    uint64_t pushed = 0, pulled = 0;
    for (int round = 0; round < 4000; ++round) {
        uint8_t chunk[16];
        for (unsigned i = 0; i < 16u; ++i) {
            seed = seed * 1103515245u + 12345u;
            chunk[i] = (uint8_t)(20u + ((seed >> 16) % 30u));
        }
        c5vrx_clock_bridge_push(&b, chunk, sizeof(chunk), false);
        pushed += sizeof(chunk);
        while ((got = c5vrx_clock_bridge_pull(&b, out, sizeof(out))) != 0u)
            pulled += got;
    }
    assert(b.dropped_samples == 0u);       /* Capacity absorbs jitter. */
    assert(b.occupancy_max < sizeof(storage));
    printf("ratio: pushed=%llu pulled=%llu occ=%u/%u underflows=%llu\n",
           (unsigned long long)pushed, (unsigned long long)pulled,
           b.occupancy_min, b.occupancy_max,
           (unsigned long long)b.underflows);
    /* Pulled tracks pushed * 1000/1001 within a few samples. */
    const double expect = (double)pushed * 1000.0 / 1001.0;
    assert((double)pulled > expect - 8.0 && (double)pulled < expect + 8.0);

    /* ---- Chroma safety A/B: near-chroma-frequency tone through a
     * mismatched ratio; cubic must retain at least as much amplitude
     * as linear. */
    cfg.input_rate = 21u;
    cfg.output_rate = 20u;
    const double freq_cycles_per_input = 0.18;
    static uint8_t tone_in[6000];
    for (unsigned i = 0; i < sizeof(tone_in); ++i) {
        tone_in[i] =
            (uint8_t)(31.5 + 28.0 * sin(2.0 * 3.14159265358979 *
                                        freq_cycles_per_input * (double)i));
    }
    double rms[2];
    const char *names[2] = {"linear", "cubic"};
    for (int method = 0; method < 2; ++method) {
        cfg.method = (uint8_t)method;
        assert(c5vrx_clock_bridge_init(&b, &cfg));
        c5vrx_clock_bridge_push(&b, tone_in, 512u, true); /* Prime. */
        static uint8_t got_buf[sizeof(tone_in) * 4];
        size_t off = 0;
        while (off < sizeof(got_buf)) {
            got = c5vrx_clock_bridge_pull(&b, got_buf + off, 256u);
            if (!got) break;
            off += got;
        }
        rms[method] = tone_rms(got_buf + 256, 2048, NULL);
        printf("%s: rms=%.2f\n", names[method], rms[method]);
    }
    assert(rms[1] >= rms[0]);

    /* ---- Overload drops are counted and latency stays bounded. */
    cfg.input_rate = 1000u;
    cfg.output_rate = 500u; /* Consumer half rate: FIFO must fill. */
    cfg.method = C5VRX_BRIDGE_LINEAR;
    assert(c5vrx_clock_bridge_init(&b, &cfg));
    memset(in, 31, sizeof(in));
    for (int r = 0; r < 200; ++r) c5vrx_clock_bridge_push(&b, in, 32u, true);
    assert(b.dropped_samples > 0u);
    assert(b.occupancy_max <= sizeof(storage));

    puts("clock_bridge_test: PASS");
    return 0;
}
