/* C5VRX by Twotoz and contributors. Independent scalar recurrence versus
 * every resumable split; this test does not establish a live DMA deadline. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../main/sync_pulse_walk.h"

#define N 434u
static uint32_t random_state = 213u;
static uint64_t checks;
static uint32_t random_word(void)
{
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state;
}

typedef struct {
    bool low[N];
    uint64_t start;
    uint32_t count, limit, pending_at, expire_at;
} source_t;

static spw_sample_result_t sample(void *context, uint64_t k, bool *low)
{
    source_t *s = context;
    assert(s->count < s->limit);
    assert(k == s->start + 3u * s->count);
    if (s->count == s->expire_at) return SPW_EXPIRED;
    if (s->count == s->pending_at) return SPW_PENDING;
    *low = s->low[s->count++];
    return SPW_SAMPLE;
}

/* The original recurrence is kept independent of the state-machine helper. */
static void oracle(const bool *low, uint32_t max, uint32_t *width,
                   uint32_t *inside, uint32_t *count)
{
    uint32_t w = 0, highs = 0, total = 0, n = 0;
    while (w < max && highs < 2u) {
        if (low[n++]) highs = 0;
        else { ++highs; ++total; }
        w += 3u;
    }
    *width = w - 3u * highs;
    *inside = total - highs;
    *count = n;
}

static void complete(spw_t *walk, source_t *s, uint32_t width,
                     uint32_t inside, uint32_t count)
{
    uint32_t used = UINT32_MAX, got_width = UINT32_MAX, got_inside = UINT32_MAX;
    assert(spw_run(walk, sample, s, UINT32_MAX, &used) == SPW_DONE);
    assert(spw_result(walk, &got_width, &got_inside));
    assert(got_width == width && got_inside == inside && s->count == count);
    assert(used <= count);
    assert(spw_run(walk, sample, s, UINT32_MAX, &used) == SPW_DONE && used == 0u);
    ++checks;
}

static void exercise(source_t *s, uint32_t max)
{
    uint32_t width, inside, count;
    oracle(s->low, max, &width, &inside, &count);
    s->limit = count;
    for (uint32_t split = 0; split <= count; ++split) {
        spw_t walk;
        assert(spw_init(&walk, s->start, max));
        s->count = 0u;
        s->pending_at = s->expire_at = UINT32_MAX;
        uint32_t used = UINT32_MAX;
        spw_status_t status = spw_run(&walk, sample, s, split, &used);
        assert(used == split && s->count == split);
        assert(status == (split == count ? SPW_DONE : SPW_ACTIVE));
        if (status == SPW_ACTIVE) {
            uint32_t a = 12345u, b = 54321u;
            assert(!spw_result(&walk, &a, &b) && a == 12345u && b == 54321u);
            spw_t before = walk;
            s->pending_at = split;
            assert(spw_run(&walk, sample, s, UINT32_MAX, &used) == SPW_ACTIVE);
            assert(used == 0u && memcmp(&before, &walk, sizeof(walk)) == 0);
            assert(spw_run(&walk, sample, s, 0u, &used) == SPW_ACTIVE && used == 0u);
            s->pending_at = UINT32_MAX;
        }
        complete(&walk, s, width, inside, count);
    }
    /* Repeated tiny and random budgets, including empty turns. */
    for (unsigned mode = 0; mode < 2u; ++mode) {
        spw_t walk;
        assert(spw_init(&walk, s->start, max));
        s->count = 0u;
        s->pending_at = s->expire_at = UINT32_MAX;
        unsigned turns = 0;
        while (walk.status == SPW_ACTIVE) {
            uint32_t used, budget = mode ? random_word() % 9u : 1u;
            assert(++turns < 10000u);
            (void)spw_run(&walk, sample, s, budget, &used);
            assert(used <= budget && walk.start == s->start && walk.max == max);
        }
        complete(&walk, s, width, inside, count);
    }
    /* Every possible source expiration must refuse partial results. */
    for (uint32_t split = 0; split < count; ++split) {
        spw_t walk;
        assert(spw_init(&walk, s->start, max));
        s->count = 0u;
        s->pending_at = UINT32_MAX;
        s->expire_at = split;
        uint32_t used, a = 12345u, b = 54321u;
        assert(spw_run(&walk, sample, s, UINT32_MAX, &used) == SPW_ABORTED);
        assert(used == split && s->count == split);
        assert(walk.w == 0u && walk.total == 0u && walk.highs == 0u);
        assert(!spw_result(&walk, &a, &b) && a == 12345u && b == 54321u);
        assert(spw_run(&walk, sample, s, 1u, &used) == SPW_ABORTED && used == 0u);
        ++checks;
    }
}

/* Build genuine wrapped phase increments and independently apply the nine-step
 * low-mean predicate at each sampled center. Both threshold crossings and
 * isolated glitches therefore reach the walker through the real recurrence. */
static void phase_pattern(source_t *s, unsigned seed)
{
    uint8_t phase[1315];
    phase[0] = (uint8_t)seed;
    for (unsigned j = 1; j < sizeof(phase); ++j) {
        int step = (j / (17u + seed % 23u)) % 2u ? -45 : 18;
        if (j % 83u == seed % 83u) step = 90;
        phase[j] = (uint8_t)((int)phase[j - 1u] + step);
    }
    for (unsigned j = 0; j < N; ++j) {
        int sum = 0;
        for (unsigned d = 0; d < 9u; ++d) {
            int step = (int)phase[3u * j + d + 1u] - phase[3u * j + d];
            if (step > 127) step -= 256;
            if (step < -128) step += 256;
            sum += step;
        }
        s->low[j] = sum <= -9 * 12;
    }
}

int main(void)
{
    source_t s = {.start = UINT64_C(1234567890123)};
    for (unsigned pattern = 0; pattern < 9u; ++pattern) {
        for (unsigned j = 0; j < N; ++j) {
            switch (pattern) {
            case 0: s.low[j] = true; break;
            case 1: s.low[j] = false; break;
            case 2: s.low[j] = (j % 2u) == 0u; break;
            case 3: s.low[j] = j % 11u != 3u; break;
            case 4: s.low[j] = j < 100u || j == 101u; break;
            case 5: s.low[j] = (random_word() & 7u) != 0u; break;
            default: s.low[j] = true; break;
            }
        }
        if (pattern >= 6u) phase_pattern(&s, pattern * 29u);
        for (uint32_t max = 0; max <= 1300u; ++max) exercise(&s, max);
    }
    /* Terminal highs are excluded, while interior single highs remain inside. */
    spw_t walk;
    uint32_t a, b, used;
    s.start = 0u; s.limit = 5u; s.count = 0u;
    s.pending_at = s.expire_at = UINT32_MAX;
    s.low[0] = true; s.low[1] = false; s.low[2] = true;
    s.low[3] = false; s.low[4] = false;
    assert(spw_init(&walk, 0u, 100u));
    assert(spw_run(&walk, sample, &s, 5u, &used) == SPW_DONE && used == 5u);
    assert(spw_result(&walk, &a, &b) && a == 9u && b == 1u);
    assert(!spw_init(NULL, 0u, 1u));
    assert(!spw_init(&walk, 0u, UINT32_MAX));
    assert(!spw_init(&walk, 0u, UINT32_MAX - 1u));
    assert(spw_init(&walk, 0u, UINT32_MAX - 2u));
    assert(!spw_init(&walk, UINT64_MAX, 4u));
    assert(!spw_init(&walk, UINT64_MAX - 2u, 4u));
    assert(spw_init(&walk, UINT64_MAX, 3u));
    s.start = UINT64_MAX; s.count = 0u; s.limit = 1u; s.low[0] = true;
    assert(spw_run(&walk, sample, &s, 1u, &used) == SPW_DONE);
    assert(spw_result(&walk, &a, &b) && a == 3u && b == 0u);
    assert(spw_init(&walk, UINT64_MAX, 0u));
    assert(spw_run(&walk, NULL, NULL, 1u, &used) == SPW_DONE && used == 0u);
    assert(spw_result(&walk, &a, &b) && a == 0u && b == 0u);
    assert(spw_init(&walk, 0u, 10u));
    assert(spw_run(&walk, NULL, NULL, 1u, &used) == SPW_ABORTED);
    assert(!spw_result(&walk, &a, &b));
    printf("PASS: %llu exact pulse-width split/expiry cases; max 0..1300, "
           "phase-derived inputs, frozen bounds, no hardware claim\n",
           (unsigned long long)checks);
    return 0;
}
