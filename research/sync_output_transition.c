/* C5VRX by Twotoz/contributors. Offline transition audit, not a live driver.
 * Include the actual candidate so this exercises its current synthesis and
 * first-EQ bootstrap, instead of a separately reimplemented carrier formula.
 * Non-LP rows are diagnostic: the candidate currently assumes LP bootstrap. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "phase8_gain_lut.h"
#include "kp_tables.h"
#include "output_level.h"
#include "../main/sync_flywheel.c"

#define RING 32768u
#define LENGTH 2304u
#define START 512u
static uint8_t raw[RING], before[RING], decoded[LENGTH], baseline[LENGTH];

/* Independent actual BS controller/worker recurrence. Non-LP controller
 * emits previous LUT DAC; LP emits counter A, including quadrant carry. */
static void decode(const uint8_t *source, uint8_t *dest, const uint16_t *lut,
                   bool lp, unsigned parity, unsigned initial_phase, unsigned initial_level)
{
    unsigned phase = initial_phase, x = initial_level, y = initial_level;
    for (unsigned k = parity; k + 1u < LENGTH; k += 2u) {
        dest[k] = dest[k + 1u] = (uint8_t)(lp ? y : x);
        unsigned a = source[k], b = source[k + 1u];
        unsigned address = (a >> 4) | ((a & 15u) << 4) | ((b & 128u) << 1) | ((b & 8u) << 6);
        unsigned token = lut[address] >> 11;
        unsigned carry = ((address >> 8) + (phase >> 3)) >> 2;
        unsigned value = lut[phase * 32u + token];
        y = (y >> 1) + (x >> 1) + carry;
        x = value & 63u; phase = (value >> 6) & 31u;
    }
}

static unsigned audit_pulse_width(unsigned position, unsigned parity, unsigned limit)
{
    unsigned best = 0;
    for (unsigned k = position - 15u; k <= position + 15u; ++k) {
        if ((k & 1u) != parity || decoded[k] > limit) continue;
        unsigned n = 0, highs = 0;
        for (unsigned j = k; j + 1u < LENGTH && highs < 2u; j += 2u) {
            if (decoded[j] <= limit) highs = 0; else ++highs;
            n += 2u;
        }
        n -= 2u * highs;
        if (n > best) best = n;
    }
    return best;
}

int main(void)
{
    puts("kp,lp,output,pal,kind,cases,relative_width_ok,lp_bound_width_ok,normalized_width_ok,legacy_width_ok,bootstrap_refused,tip_floor,tip_ceiling,blank,relative_limit,lut_clipped,tail_changed,max_tail_span");
    for (unsigned kp = 0; kp < KP_LEVELS; ++kp) for (unsigned output = 0; output < OUTPUT_LEVELS; ++output) {
        uint16_t lut[1024]; unsigned clipped = 0;
        for (unsigned j = 0; j < 1024; ++j) {
            bool clip; lut[j] = output_entry(kp_tables[kp][j], output, &clip); clipped += clip;
            assert((lut[j] & 0xffc0u) == (kp_tables[kp][j] & 0xffc0u));
        }
        unsigned relative_limit = output_entry(8u, output, NULL) & 63u;
        unsigned blank = output_entry(21u, output, NULL) & 63u;
        unsigned sync_floor = output_entry(0u, output, NULL) & 63u;
        /* Fixed legacy8/21 crossing fraction in the independently mapped
         * sync-to-blank interval. This is a comparison metric, not a claim
         * that a physical monitor uses that crossing or loaded voltage. */
        unsigned normalized_limit = sync_floor + (8u * (blank-sync_floor) + 10u) / 21u;
        for (unsigned lp = 0; lp < 2; ++lp) for (unsigned pal_mode = 0; pal_mode < 2; ++pal_mode)
            for (unsigned kind = 0; kind < 4; ++kind) {
                unsigned cases = 0, relative_ok = 0, lp_bound_ok = 0, normalized_ok = 0, legacy_ok = 0, refused = 0;
                unsigned tip_floor = 63, tip_ceiling = 0, tail_changed = 0, max_tail_span = 0;
                unsigned half = pal_mode ? 1280u : 1271u;
                unsigned tip = kind == 0 ? 188u : kind == 2 ? half - 188u : 94u;
                unsigned n1 = kind == 0 ? 48u : tip, n2 = kind == 0 ? tip : half - tip;
                unsigned position = START + (kind == 0 ? 48u : 0u);
                unsigned width_lo = kind == 0 ? 150u : kind == 2 ? tip - 60u : 70u;
                unsigned width_hi = kind == 0 ? 230u : kind == 2 ? tip + 40u : 125u;
                for (unsigned seed = 0; seed < 8; ++seed) {
                    sfw_ring_t r = {.ring=raw,.ring_bytes=RING,.phase=c5vrx_phase8_gain_lut,
                        .verified_kp_x100=kp_level_x100[kp],.program_verified=true,.decoder_lut=lut};
                    build_cells(&r);
                    /* A real original phase prefix and suffix at the configured
                     * -1MHz blanking target. No output-derived threshold fit. */
                    uint32_t phase = seed * 8192u;
                    for (unsigned k = 0; k < RING; ++k) {
                        phase = (phase - 1638u) & 65535u;
                        raw[k] = s_cell[(uint8_t)((phase + 128u) >> 8)];
                    }
                    memcpy(before, raw, RING);
                    sync_flywheel_t f; sfw_init(&f);
                    f.blank_q4 = -102;
                    f.repair_step_q8 = verified_sync_step(&r); f.repair_kp_x100 = kp_level_x100[kp];
                    f.evidence_end = LENGTH;
                    bool ok = synth(&f,&r,START,n1,kind == 0 ? -1632 : f.repair_step_q8,
                        n2,kind == 0 ? f.repair_step_q8 : -1632,-1632,kind == 3,0,LENGTH);
                    assert(ok); /* These are preparation-only private arrays. */
                    refused += f.bootstrap_refused;
                    assert(!memcmp(before,raw,START));
                    assert(!memcmp(before+START+n1+n2,raw+START+n1+n2,RING-START-n1-n2));
                    for (unsigned parity = 0; parity < 2; ++parity)
                        for (unsigned initial = 0; initial < 32; ++initial)
                            for (unsigned level = 0; level <= 63u; level += 63u) {
                                memset(decoded,0,sizeof(decoded)); memset(baseline,0,sizeof(baseline));
                                decode(raw,decoded,lut,lp,parity,initial,level);
                                decode(before,baseline,lut,lp,parity,initial,level);
                                unsigned width = audit_pulse_width(position,parity,relative_limit);
                                unsigned legacy = audit_pulse_width(position,parity,8u);
                                /* Independent integer LP invariant: x<=L implies
                                 * y<=L+1 once settled, because y'=floor(y/2)+
                                 * floor(x/2)+carry and carry is0or1. */
                                unsigned lp_limit = relative_limit + (lp && relative_limit < 63u);
                                unsigned bound = audit_pulse_width(position,parity,lp_limit);
                                unsigned normalized = audit_pulse_width(position,parity,normalized_limit);
                                ++cases;
                                relative_ok += width >= width_lo && width <= width_hi;
                                lp_bound_ok += bound >= width_lo && bound <= width_hi;
                                normalized_ok += normalized >= width_lo && normalized <= width_hi;
                                legacy_ok += legacy >= width_lo && legacy <= width_hi;
                                for (unsigned k = position + 24u; k + 16u < position + tip; ++k) {
                                    if (decoded[k] < tip_floor) tip_floor = decoded[k];
                                    if (decoded[k] > tip_ceiling) tip_ceiling = decoded[k];
                                }
                                for (unsigned k = START+n1+n2; k < START+n1+n2+256u; ++k) {
                                    if (decoded[k] != baseline[k]) {
                                        ++tail_changed;
                                        unsigned span = k - START - n1 - n2 + 1u;
                                        if (span > max_tail_span) max_tail_span = span;
                                    }
                                }
                            }
                }
                printf("%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u\n",
                    kp_level_x100[kp],lp,output,pal_mode,kind,cases,relative_ok,lp_bound_ok,normalized_ok,legacy_ok,
                    refused,tip_floor,tip_ceiling,blank,relative_limit,clipped,tail_changed,max_tail_span);
            }
    }
    return 0;
}
