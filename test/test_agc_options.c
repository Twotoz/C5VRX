/* C5VRX by Twotoz/contributors: runtime LEVEL controls, no RF claims. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "agc.h"

static agc_level_t baseline, configured, shifted;
int main(void)
{
    int16_t gain[DG_STATES];
    for (unsigned i = 0; i < DG_STATES; ++i) gain[i] = (int16_t)(i * 256);
    agc_level_reset(&baseline, gain, 83, 77);
    agc_level_reset(&configured, gain, 83, 77);
    agc_level_options(&configured, false, 0, 0, 0);
    assert(!memcmp(&baseline, &configured, sizeof(baseline)));
    uint8_t a = 75, b = 75;
    for (unsigned i = 1; i <= 2000; ++i) {
        dg_observation_t o = {.pwr_x16 = (uint16_t)(20 + i % 500), .observed_us = i * 200u};
        if (i % 111 == 0) { o.rails = 15; o.clip_pm = 100; }
        a = agc_level_tick(&baseline, &o, a, o.observed_us);
        b = agc_level_tick(&configured, &o, b, o.observed_us);
        assert(a == b && !memcmp(&baseline, &configured, sizeof(baseline)));
    }
    /* The target changes the quality scoring, without changing the physical
     * gain/noise scale or the learned gain corrections. Reset removes it. */
    shifted = baseline;
    agc_level_options(&shifted, false, 0, 3, 0);
    assert(memcmp(shifted.quality, baseline.quality, sizeof(shifted.quality)));
    assert(!memcmp(shifted.rel_q8, baseline.rel_q8, sizeof(shifted.rel_q8)));
    assert(!memcmp(shifted.noise_x16, baseline.noise_x16, sizeof(shifted.noise_x16)));
    assert(!memcmp(shifted.learned_q8, baseline.learned_q8, sizeof(shifted.learned_q8)));
    /* The same clean sample ages history at the requested release rate. */
    configured = baseline; shifted = baseline;
    agc_level_options(&configured, false, 0, 0, 1);
    agc_level_options(&shifted, false, 0, 0, 2);
    for (unsigned i = 0; i < AGC_BINS; ++i) configured.hist[i] = shifted.hist[i] = configured.fast[i] = shifted.fast[i] = 10000;
    const dg_observation_t clean = {.pwr_x16=80, .observed_us=1000000};
    (void)agc_level_tick(&configured, &clean, 83, clean.observed_us);
    (void)agc_level_tick(&shifted, &clean, 83, clean.observed_us);
    assert(configured.hist[0] > shifted.hist[0]);
    /* Fold memory is stamped from the observation clock, and reset clears
     * it. The independent instances do not leak runtime policy. */
    agc_level_reset(&configured, gain, 83, 77);
    agc_level_options(&configured, true, 300000, 0, 0);
    const dg_observation_t fold = {.pwr_x16=400, .rails=15, .clip_pm=100, .observed_us=2000000};
    (void)agc_level_tick(&configured, &fold, 75, fold.observed_us);
    assert(configured.fold_row==75 && configured.fold_until_us==2300000);
    agc_level_reset(&configured, gain, 83, 77);
    assert(!configured.fold_radius && !configured.fold_memory_us && !configured.fold_until_us);
    puts("PASS: runtime LEVEL defaults, scoring scale, release history and fold memory");
}
