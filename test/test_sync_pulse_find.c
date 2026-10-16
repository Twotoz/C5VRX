/* C5VRX by Twotoz and contributors. Independent scalar pulse finder versus
 * resumable observation. This proves algorithm equivalence, not DMA timing. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "sync_pulse_find.h"

#define SIZE 8192u
#define TRACE 2048u
#define PRED 4096u
typedef struct {
    bool low[SIZE];
    uint64_t trace[TRACE];
    unsigned count, at, allowance, expire;
} source_t;
static unsigned long long checks, pauses;
static unsigned stages;
static unsigned rng = 213u;
static unsigned random_word(void)
{
    rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng;
}
static bool observe(source_t *s, uint64_t k)
{
    assert(k < SIZE && s->count < TRACE);
    s->trace[s->count++] = k;
    return s->low[k];
}
/* Deliberately retains the original scalar loops and classification, with
 * no calls to either production state-machine helper. */
static spf_pulse_t scalar(source_t *s, uint64_t pred, int win,
                           uint64_t avail, int *edge, uint32_t *width)
{
    if (pred + (unsigned)win + 8u > avail) return SPF_PULSE_NONE;
    bool previous = observe(s, pred - (unsigned)win - 3u);
    for (int e = -win; e <= win; e += 3) {
        uint64_t k = pred + (uint64_t)(int64_t)e;
        bool current = observe(s, k);
        if (current && !previous && observe(s, k + 3u)) {
            uint64_t room = avail > k + 12u ? avail - k - 12u : 0;
            uint32_t max = room < 1300u ? (uint32_t)room : 1300u;
            uint32_t w = 0, highs = 0, total = 0;
            while (w < max && highs < 2u) {
                if (observe(s, k + w)) highs = 0;
                else { ++highs; ++total; }
                w += 3u;
            }
            uint32_t inside = total - highs;
            w -= 3u * highs;
            uint32_t n = w / 3u;
            spf_pulse_t type = SPF_PULSE_NONE;
            if (w >= 150u && w <= 230u && inside <= n / 8u)
                type = SPF_PULSE_NORMAL;
            else if (w >= 900u && inside <= n / 8u) type = SPF_PULSE_BROAD;
            else if (w >= 70u && w <= 120u && inside <= 1u) type = SPF_PULSE_EQ;
            if (type != SPF_PULSE_NONE) {
                *edge = e;
                if (observe(s, k - 2u)) *edge = e - 2;
                else if (observe(s, k - 1u)) *edge = e - 1;
                *width = w;
                return type;
            }
        }
        previous = current;
    }
    return SPF_PULSE_NONE;
}
static spw_sample_result_t sample(void *context, uint64_t k, bool *low)
{
    source_t *s = context;
    /* Includes PENDING and EXPIRED requests: neither may skip/reorder the
     * scalar request, and PENDING never consumes source evidence. */
    assert(s->at < s->count && k == s->trace[s->at]);
    if (s->at == s->expire) return SPW_EXPIRED;
    if (!s->allowance) { ++pauses; return SPW_PENDING; }
    --s->allowance;
    *low = s->low[k]; ++s->at;
    return SPW_SAMPLE;
}
static void hidden(const spf_t *f)
{
    spf_pulse_t type = SPF_PULSE_BROAD;
    int edge = 123456;
    uint32_t width = 987654;
    assert(!spf_result(f, &type, &edge, &width));
    assert(type == SPF_PULSE_BROAD && edge == 123456 && width == 987654);
    ++checks;
}
static void finish(spf_t *f, source_t *s, spf_pulse_t expected,
                    int expected_edge, uint32_t expected_width)
{
    spf_pulse_t type = SPF_PULSE_BROAD;
    int edge = 123456;
    uint32_t width = 987654;
    assert(spf_result(f, &type, &edge, &width));
    assert(type == expected && s->at == s->count);
    assert(edge == expected_edge && width == expected_width);
    assert(spf_run(f, sample, s) == SPF_DONE && s->at == s->count);
    ++checks;
}
static void exercise(source_t *s, int window, uint64_t avail, bool exhaustive)
{
    int expected_edge = 123456;
    uint32_t expected_width = 987654;
    s->count = 0;
    spf_pulse_t expected = scalar(s, PRED, window, avail,
                                  &expected_edge, &expected_width);
    s->expire = ~0u;
    spf_t f;
    /* Every successful-observation boundary, including zero and final, is
     * tested as a yield point. Repeated yields at that point must be inert. */
    unsigned stride = exhaustive ? 1u : 17u;
    for (unsigned split = 0; split <= s->count; split += stride) {
        assert(spf_init(&f, PRED, window, avail));
        s->at = 0; s->allowance = split;
        spf_status_t status = spf_run(&f, sample, s);
        if (status != SPF_DONE) {
            assert(status != SPF_ABORTED && s->at == split);
            hidden(&f); stages |= 1u << status;
            spf_t saved = f;
            assert(spf_run(&f, sample, s) == status);
            assert(!memcmp(&saved, &f, sizeof(f)));
            s->allowance = ~0u;
            assert(spf_run(&f, sample, s) == SPF_DONE);
        }
        finish(&f, s, expected, expected_edge, expected_width);
    }
    static const unsigned quotas[] = {1, 2, 3, 7, 31, 52, 64};
    for (unsigned q = 0; q < sizeof(quotas) / sizeof(quotas[0]); ++q) {
        assert(spf_init(&f, PRED, window, avail)); s->at = 0;
        unsigned iterations = 0;
        do {
            s->allowance = quotas[q];
            spf_status_t status = spf_run(&f, sample, s);
            assert(status != SPF_ABORTED);
            if (status != SPF_DONE) hidden(&f);
            assert(++iterations < TRACE);
        } while (f.status != SPF_DONE);
        finish(&f, s, expected, expected_edge, expected_width);
    }
    /* Expiry at every source read, including both edge refinements, must
     * abandon partial evidence and remain terminal on subsequent calls. */
    if (exhaustive) for (unsigned expire = 0; expire < s->count; ++expire) {
        assert(spf_init(&f, PRED, window, avail));
        s->at = 0; s->expire = expire; s->allowance = ~0u;
        assert(spf_run(&f, sample, s) == SPF_ABORTED);
        assert(s->at == expire); hidden(&f);
        assert(spf_run(&f, sample, s) == SPF_ABORTED && s->at == expire);
    }
    s->expire = ~0u;
}
static void pulse(source_t *s, unsigned start, unsigned width)
{
    assert(start + width < SIZE);
    for (unsigned k = start; k < start + width; ++k) s->low[k] = true;
}
static int wrap(int x) { return (x + 128) % 256 - 128; }
int main(void)
{
    static source_t s;
    const int windows[] = {16, 24, 96};
    const unsigned widths[] = {0, 1, 2, 3, 69, 70, 71, 72, 119, 120, 121,
        149, 150, 151, 188, 229, 230, 231, 899, 900, 901, 1299, 1300, 1301};
    for (unsigned w = 0; w < sizeof(windows) / sizeof(windows[0]); ++w)
        for (unsigned n = 0; n < sizeof(widths) / sizeof(widths[0]); ++n)
            for (int delta = -2; delta <= 2; ++delta) {
                memset(&s, 0, sizeof(s));
                pulse(&s, PRED + (unsigned)delta, widths[n]);
                exercise(&s, windows[w], PRED + 1600u, true);
            }
    /* Every in-band integer width, rather than merely representative points,
     * crossed with all yield boundaries. The window and edge alignment vary. */
    const unsigned bands[][2] = {{70, 120}, {150, 230}, {900, 1300}};
    for (unsigned b = 0; b < sizeof(bands) / sizeof(bands[0]); ++b)
        for (unsigned width = bands[b][0]; width <= bands[b][1]; ++width) {
            memset(&s, 0, sizeof(s));
            pulse(&s, PRED - 2u + width % 5u, width);
            exercise(&s, windows[width % 3u], PRED + 1600u, true);
        }
    /* Bad candidate, then valid equalizing pulse in the same search window;
     * and one tolerated interior high versus excessive interior defects. */
    for (unsigned holes = 0; holes <= 12; ++holes) {
        memset(&s, 0, sizeof(s));
        pulse(&s, PRED - 90u, 15u); pulse(&s, PRED - 30u, 90u);
        for (unsigned j = 0; j < holes; ++j) s.low[PRED - 21u + 6u*j] = false;
        exercise(&s, 96, PRED + 1600u, true);
    }
    /* Frozen source room cuts through a broad run. Increasing availability
     * on later service turns must require a new finder, not extend this one. */
    for (unsigned room = 0; room <= 1320; room += 3) {
        memset(&s, 0, sizeof(s)); pulse(&s, PRED, 1450);
        exercise(&s, 16, PRED + room, room % 60u == 0);
    }
    for (unsigned seed = 0; seed < 160; ++seed) {
        memset(&s, 0, sizeof(s));
        for (unsigned k = 0; k < SIZE; ++k) s.low[k] = (random_word() & 7u) < 3u;
        if (seed & 1u) pulse(&s, PRED + seed % 7u, 188u);
        exercise(&s, windows[seed % 3u], PRED + 1600u, false);
    }
    /* Independent nine-step phase predicates: modulo-256 phase samples have
     * sync/blank frequency steps plus jitter; no direct bool rectangle is
     * handed to the finder. Includes phase wraps and threshold crossings. */
    for (unsigned scene = 0; scene < 32; ++scene) {
        unsigned phase[SIZE], p = scene * 7u;
        unsigned start = PRED - 2u + scene % 5u;
        unsigned width = (scene & 1u) ? 188u : 94u;
        memset(&s, 0, sizeof(s));
        for (unsigned k = 0; k < SIZE; ++k) {
            int step = k >= start && k < start + width ? -30 : -7;
            step += (int)(random_word() % 7u) - 3;
            p = (p + (unsigned)step) & 255u; phase[k] = p;
        }
        for (unsigned k = 5; k + 4u < SIZE; ++k) {
            int sum = 0;
            for (unsigned j = k - 4u; j <= k + 4u; ++j)
                sum += wrap((int)phase[j] - (int)phase[j - 1u] + 256);
            s.low[k] = sum <= 9 * -20;
        }
        exercise(&s, windows[scene % 3u], PRED + 1600u, true);
    }
    assert((stages & ((1u << SPF_REFINE1) + (1u << SPF_REFINE2) +
        (1u << SPF_WIDTH) + (1u << SPF_CONFIRM) + (1u << SPF_CURRENT) +
        (1u << SPF_PREVIOUS))) == 63u);
    spf_t f;
    assert(!spf_init(NULL, PRED, 16, SIZE));
    assert(!spf_init(&f, 0, 16, SIZE));
    assert(!spf_init(&f, PRED, -1, SIZE));
    assert(!spf_init(&f, UINT64_MAX, 16, UINT64_MAX));
    printf("PASS: %llu pulse-finder checks, %llu pending callbacks, all six stages\n",
           checks, pauses);
    return 0;
}
