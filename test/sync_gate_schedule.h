/* C5VRX by Twotoz/contributors: test fixture (formerly main/sync_gate_schedule.h,
 * the removed Z/z gate bench) that generates PAL/NTSC pulse schedules for the
 * native-track host tests.
 * Diagnostic timing only: this is not camera acquisition, phase alignment,
 * analogue pulse qualification, or proof of monitor lock. No GPIO/timer I/O.
 * Absolute rational time prevents field-by-field rounding accumulation. */
#ifndef C5VRX_SYNC_GATE_SCHEDULE_H
#define C5VRX_SYNC_GATE_SCHEDULE_H
#include <stdbool.h>
#include <stdint.h>

#define SGS_TIMER_HZ 20000000u
typedef enum { SGS_PAL, SGS_NTSC } sgs_standard_t;
typedef enum { SGS_EQ, SGS_BROAD, SGS_NORMAL } sgs_kind_t;
typedef struct {
    uint64_t start, field, half;
    uint32_t width;
    uint16_t index;             /* Half-line index within the field. */
    sgs_kind_t kind;
} sgs_pulse_t;
typedef struct {
    uint64_t next_half, next_tick, field;
    uint16_t index;
    uint8_t remainder;
    sgs_standard_t standard;
    bool exhausted;
} sgs_t;

/* NTSC is round(half * 5720 / 9) ticks, PAL exactly half * 640.
 * Split quotient/remainder before multiplying so long runs cannot wrap. */
static inline bool sgs_half_tick(sgs_standard_t standard, uint64_t half,
                                  uint64_t *tick)
{
    if (!tick) return false;
    if (standard == SGS_PAL) {
        if (half > UINT64_MAX / 640u) return false;
        *tick = half * 640u;
        return true;
    }
    if (standard != SGS_NTSC) return false;
    uint64_t quotient = half / 9u;
    uint64_t remainder = ((half % 9u) * 5720u + 4u) / 9u;
    if (quotient > (UINT64_MAX - remainder) / 5720u) return false;
    *tick = quotient * 5720u + remainder;
    return true;
}

/* Initialization/seeking is outside the ISR and may use wide division. All
 * sequential sgs_next calls use only counters, add/subtract and comparisons.
 * remainder starts at four to encode nearest rounding, then carries five
 * ninths per NTSC half-line. This is exact integer arithmetic, not a rounded
 * half-line increment whose error could accumulate between fields. */
static inline bool sgs_init_at(sgs_t *s, sgs_standard_t standard, uint64_t half)
{
    if (!s) return false;
    *s = (sgs_t){.standard = standard, .exhausted = true};
    if (!sgs_half_tick(standard, half, &s->next_tick)) return false;
    uint16_t halves = standard == SGS_PAL ? 625u : 525u;
    s->next_half = half;
    s->field = half / halves;
    s->index = (uint16_t)(half % halves);
    s->remainder = standard == SGS_PAL ? 0u : (uint8_t)(((half % 9u) * 5u + 4u) % 9u);
    s->exhausted = false;
    return true;
}
static inline bool sgs_init(sgs_t *s, sgs_standard_t standard)
{
    return sgs_init_at(s, standard, 0u);
}

static inline uint32_t sgs_half_length(const sgs_t *s)
{
    return s->standard == SGS_PAL ? 640u : (s->remainder >= 4u ? 636u : 635u);
}
static inline void sgs_advance(sgs_t *s)
{
    uint32_t duration = sgs_half_length(s);
    if (s->next_half == UINT64_MAX || s->next_tick > UINT64_MAX - duration) {
        s->exhausted = true; return;
    }
    ++s->next_half;
    s->next_tick += duration;
    if (++s->index == (s->standard == SGS_PAL ? 625u : 525u)) {
        s->index = 0u; ++s->field;
    }
    if (s->standard == SGS_NTSC) {
        s->remainder += 5u;
        if (s->remainder >= 9u) s->remainder -= 9u;
    }
}

/* Emit only actual pulses; ordinary odd half-lines have no pulse. Global
 * parity, rather than field-local parity, preserves the interlaced H grid.
 * Refusal leaves the output untouched and latches an exhausted iterator. */
static inline bool sgs_next(sgs_t *s, sgs_pulse_t *pulse)
{
    if (!s || !pulse || s->exhausted) return false;
    if (s->standard != SGS_PAL && s->standard != SGS_NTSC) {
        s->exhausted = true; return false;
    }
    uint16_t section = s->standard == SGS_PAL ? 5u : 6u;
    if (s->index >= 3u * section && (s->next_half & 1u)) {
        sgs_advance(s);
        if (s->exhausted) return false;
    }
    sgs_pulse_t out = {.half = s->next_half, .index = s->index,
        .field = s->field, .start = s->next_tick, .kind = SGS_NORMAL, .width = 94u};
    if (s->index < 3u * section) {
        if (s->index >= section && s->index < 2u * section) {
            uint32_t duration = sgs_half_length(s);
            if (s->next_half == UINT64_MAX || s->next_tick > UINT64_MAX - duration) {
                s->exhausted = true; return false;
            }
            out.kind = SGS_BROAD;
            out.width = duration - 94u;
        } else {
            out.kind = SGS_EQ;
            out.width = s->standard == SGS_PAL ? 47u : 46u;
        }
    }
    if (out.start > UINT64_MAX - out.width) {
        s->exhausted = true; return false;
    }
    *pulse = out;
    sgs_advance(s);
    return true;
}
#endif
