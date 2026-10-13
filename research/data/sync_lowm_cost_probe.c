/* C5VRX by Twotoz/contributors: generated static RV32 cost probe. */
#include "sync_flywheel.h"
#define SFW_HOT __attribute__((noinline, used))
static uint32_t s_evals, s_lowm;
static inline uint8_t ph_at(const sfw_ring_t *r, uint64_t k)
{
    return r->phase[r->ring[(uint32_t)k & (r->ring_bytes - 1u)]];
}
static inline int wrap_baseline(int d)
{
    d &= 255;
    return d >= 128 ? d - 256 : d;
}
static SFW_HOT bool baseline(const sync_flywheel_t *f, const sfw_ring_t *r, uint64_t k)
{
    ++s_evals;
    ++s_lowm;
    int total = 0;
    /* Center on the tested sample. The legacy [-3,+6] window made the
     * detected edge two samples early and could overwrite the last real
     * picture samples before the vertical interval. */
    int previous = ph_at(r, k - 5u);
    for (unsigned j = 0; j < 9u; ++j) {
        int phase = ph_at(r, k - 4u + j);
        total += wrap_baseline(phase - previous);
        previous = phase;
    }
    return total <= 9 * f->thr;
}
static inline int wrap_branchless(int d) { return ((d + 128) & 255) - 128; }
static SFW_HOT bool branchless(const sync_flywheel_t *f, const sfw_ring_t *r, uint64_t k)
{
    ++s_evals;
    ++s_lowm;
    int total = 0;
    /* Center on the tested sample. The legacy [-3,+6] window made the
     * detected edge two samples early and could overwrite the last real
     * picture samples before the vertical interval. */
    int previous = ph_at(r, k - 5u);
    for (unsigned j = 0; j < 9u; ++j) {
        int phase = ph_at(r, k - 4u + j);
        total += wrap_branchless(phase - previous);
        previous = phase;
    }
    return total <= 9 * f->thr;
}
static inline int wrap_unrolled(int d)
{
    d &= 255;
    return d >= 128 ? d - 256 : d;
}
static SFW_HOT bool unrolled(const sync_flywheel_t *f, const sfw_ring_t *r, uint64_t k)
{
    ++s_evals;
    ++s_lowm;
    int total = 0;
    /* Center on the tested sample. The legacy [-3,+6] window made the
     * detected edge two samples early and could overwrite the last real
     * picture samples before the vertical interval. */
    int previous = ph_at(r, k - 5u);
    #pragma GCC unroll 9
    for (unsigned j = 0; j < 9u; ++j) {
        int phase = ph_at(r, k - 4u + j);
        total += wrap_unrolled(phase - previous);
        previous = phase;
    }
    return total <= 9 * f->thr;
}
static inline int wrap_both(int d) { return ((d + 128) & 255) - 128; }
static SFW_HOT bool both(const sync_flywheel_t *f, const sfw_ring_t *r, uint64_t k)
{
    ++s_evals;
    ++s_lowm;
    int total = 0;
    /* Center on the tested sample. The legacy [-3,+6] window made the
     * detected edge two samples early and could overwrite the last real
     * picture samples before the vertical interval. */
    int previous = ph_at(r, k - 5u);
    #pragma GCC unroll 9
    for (unsigned j = 0; j < 9u; ++j) {
        int phase = ph_at(r, k - 4u + j);
        total += wrap_both(phase - previous);
        previous = phase;
    }
    return total <= 9 * f->thr;
}
#ifdef HOST_CHECK
#include <assert.h>
#include <stdio.h>
int main(void) {
    for (int d=-255; d<=255; ++d) assert(wrap_baseline(d)==wrap_branchless(d));
    uint8_t raw[1024], phase[256]; unsigned random=71;
    sfw_ring_t r = {.ring=raw, .ring_bytes=1024, .phase=phase};
    sync_flywheel_t f = {0};
    for (unsigned j=0;j<256;++j) phase[j]=(uint8_t)(j*179u+17u);
    for (unsigned trial=0;trial<32;++trial) {
        for (unsigned j=0;j<1024;++j) {
            random=random*1664525u+1013904223u; raw[j]=(uint8_t)(random>>24);
        }
        for (unsigned k=0;k<1024;++k) {
            f.thr=(int16_t)((k%161)-80);
            bool expected=baseline(&f,&r,k);
            assert(expected==branchless(&f,&r,k));
            assert(expected==unrolled(&f,&r,k));
            assert(expected==both(&f,&r,k));
        }
    }
    puts("PASS: 32768 lowm probes identical across all four variants, every wrap difference");
}
#endif
