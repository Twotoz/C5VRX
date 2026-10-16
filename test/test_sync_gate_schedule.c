/* C5VRX by Twotoz and contributors. Independent rational raster oracle.
 * Generated schedules alone do not prove camera alignment or monitor lock. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "sync_gate_schedule.h"

static unsigned long long checks;
/* A wider independent multiplication deliberately differs from the helper's
 * overflow-safe quotient/remainder implementation. Host GCC/Clang only. */
static bool oracle_tick(sgs_standard_t standard, uint64_t half, uint64_t *tick)
{
    __uint128_t value = standard == SGS_PAL ? (__uint128_t)half * 640u :
        ((__uint128_t)half * 5720u + 4u) / 9u;
    if (value > UINT64_MAX) return false;
    *tick = (uint64_t)value;
    return true;
}
static void compare_tick(sgs_standard_t standard, uint64_t half)
{
    uint64_t expected = 123u, actual = 123u;
    bool valid = oracle_tick(standard, half, &expected);
    assert(sgs_half_tick(standard, half, &actual) == valid);
    assert(actual == expected);
    ++checks;
}
static void raster(sgs_standard_t standard, unsigned fields)
{
    const unsigned halves = standard == SGS_PAL ? 625u : 525u;
    const unsigned group = standard == SGS_PAL ? 5u : 6u;
    sgs_t s;
    assert(sgs_init(&s, standard));
    sgs_pulse_t previous = {0};
    bool have_previous = false;
    unsigned count[3] = {0}, field_pulses = 0;
    uint64_t expected_half = 0;
    for (unsigned field = 0; field < fields; ++field) {
        memset(count, 0, sizeof(count)); field_pulses = 0;
        for (unsigned i = 0; i < halves; ++i, ++expected_half) {
            bool vertical = i < 3u * group;
            if (!vertical && (expected_half & 1u)) continue;
            sgs_pulse_t p;
            assert(sgs_next(&s, &p));
            uint64_t start, next;
            assert(oracle_tick(standard, expected_half, &start));
            assert(oracle_tick(standard, expected_half + 1u, &next));
            sgs_kind_t kind = SGS_NORMAL;
            unsigned width = 94;
            if (vertical) {
                if (i >= group && i < 2u * group) {
                    kind = SGS_BROAD; width = (unsigned)(next - start - 94u);
                } else { kind = SGS_EQ; width = standard == SGS_PAL ? 47u : 46u; }
            }
            assert(p.start == start && p.half == expected_half && p.index == i);
            assert(p.field == field && p.kind == kind && p.width == width);
            assert(!have_previous || previous.start + previous.width <= p.start);
            if (p.kind == SGS_NORMAL) assert(!(p.half & 1u));
            if (p.index == 0) {
                assert(p.kind == SGS_EQ);
                /* Both standards have odd half-line counts: the next field
                 * begins half a line off the ongoing ordinary-H grid. */
                assert((p.half & 1u) == (field & 1u));
                if (!(field & 1u)) assert(p.half == (uint64_t)(field / 2u) * halves * 2u);
            }
            previous = p; have_previous = true;
            ++count[kind]; ++field_pulses; ++checks;
        }
        assert(count[SGS_EQ] == 2u * group && count[SGS_BROAD] == group);
        unsigned normals = 0;
        for (unsigned i = 3u * group; i < halves; ++i)
            if (!(((uint64_t)field * halves + i) & 1u)) ++normals;
        assert(count[SGS_NORMAL] == normals && field_pulses == 3u * group + normals);
        /* Explicit independent count values expose parity mistakes that a
         * shared count formula could conceal. */
        if (standard == SGS_PAL) assert(normals == 305u && field_pulses == 320u);
        else assert(normals == ((field & 1u) ? 253u : 254u));
        ++checks;
    }
}
static void limits(sgs_standard_t standard)
{
    uint64_t first_bad = standard == SGS_PAL ? UINT64_MAX / 640u + 1u :
        (uint64_t)(((__uint128_t)UINT64_MAX * 9u - 4u) / 5720u) + 1u;
    for (uint64_t half = first_bad - 20u; half < first_bad + 20u; ++half)
        compare_tick(standard, half);
    compare_tick(standard, 0); compare_tick(standard, UINT64_MAX);
    /* Directly seek the pure iterator near the end of representable time.
     * Any emitted pulse must fit completely, then terminal calls stay inert. */
    sgs_t s;
    assert(sgs_init_at(&s, standard, first_bad - 20u));
    sgs_pulse_t pulse = {.start = 123u, .width = 321u};
    unsigned iterations = 0;
    while (sgs_next(&s, &pulse)) {
        uint64_t start;
        assert(oracle_tick(standard, pulse.half, &start));
        assert(start == pulse.start && start <= UINT64_MAX - pulse.width);
        assert(++iterations < 30u); ++checks;
    }
    assert(s.exhausted);
    sgs_pulse_t saved = pulse;
    assert(!sgs_next(&s, &pulse) && !memcmp(&saved, &pulse, sizeof(pulse)));
    assert(!sgs_init_at(&s, standard, UINT64_MAX));
    assert(!sgs_next(&s, &pulse) && !memcmp(&saved, &pulse, sizeof(pulse)));
}
static void seek_sequence(sgs_standard_t standard, uint64_t half)
{
    sgs_t s;
    assert(sgs_init_at(&s, standard, half));
    unsigned halves = standard == SGS_PAL ? 625u : 525u;
    unsigned group = standard == SGS_PAL ? 5u : 6u;
    for (unsigned n = 0; n < 50u; ++n) {
        unsigned index = (unsigned)(half % halves);
        if (index >= 3u * group && (half & 1u)) { ++half; index = (unsigned)(half % halves); }
        uint64_t start, end;
        assert(oracle_tick(standard, half, &start));
        assert(oracle_tick(standard, half + 1u, &end));
        sgs_pulse_t p;
        assert(sgs_next(&s, &p));
        assert(p.half == half && p.start == start && p.index == index && p.field == half / halves);
        if (p.kind == SGS_BROAD) assert(p.width == end - start - 94u);
        ++half; ++checks;
    }
}
int main(void)
{
    assert(SGS_TIMER_HZ == 20000000u);
    raster(SGS_PAL, 2); raster(SGS_NTSC, 2);
    raster(SGS_PAL, 10000); raster(SGS_NTSC, 10000);
    limits(SGS_PAL); limits(SGS_NTSC);
    /* Huge, widely separated half indices: representable results agree with
     * a single absolute rational calculation, without an accumulation loop. */
    uint64_t random = UINT64_C(0x213c5c5);
    for (unsigned i = 0; i < 100000; ++i) {
        random ^= random << 13; random ^= random >> 7; random ^= random << 17;
        compare_tick(SGS_PAL, random);
        compare_tick(SGS_NTSC, random);
        compare_tick(SGS_NTSC, random >> 10);
        if (i < 1000u) {
            seek_sequence(SGS_PAL, random >> 12);
            seek_sequence(SGS_NTSC, random >> 12);
        }
    }
    sgs_t s;
    assert(!sgs_init(NULL, SGS_PAL));
    assert(!sgs_init(&s, (sgs_standard_t)99));
    sgs_pulse_t p = {.start = 123u};
    assert(!sgs_next(&s, &p) && p.start == 123u);
    assert(!sgs_next(NULL, &p));
    assert(sgs_init(&s, SGS_PAL) && !sgs_next(&s, NULL));
    assert(sgs_next(&s, &p) && p.start == 0);
    assert(!sgs_half_tick(SGS_PAL, 0, NULL));
    assert(!sgs_half_tick((sgs_standard_t)99, 0, &p.start));
    printf("PASS: %llu rational gate schedule checks, 20,004 fields, no overlap/drift\n", checks);
    return 0;
}
