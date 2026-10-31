/* C5VRX by Twotoz/contributors: explicit acquired-IQ rate. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#ifndef C5VRX_SAMPLE_HZ
#ifdef ESP_PLATFORM
uint32_t transport_iq_rate_hz(void);
#define C5VRX_SAMPLE_HZ (transport_iq_rate_hz())
#else
#define C5VRX_SAMPLE_HZ 40000000u
#endif
#endif
static inline uint32_t rx_rate_hz(unsigned mode)
{
    return mode==1 ? 48000000u : mode==2 ? 80000000u : 40000000u;
}
static inline unsigned rx_rate_div(unsigned mode) { return mode==1 ? 5u : mode==2 ? 3u : 6u; }
/* copy_probe_window normalizes IQ80 to the original IQ40 observer view. */
static inline uint32_t rx_rate_observer_hz(uint32_t hz) { return hz==80000000u ? 40000000u : hz; }

/* Fixed-IQ40 observer view of IQ80 capture bytes: every second byte, as the
 * gain probe. Copied from twice the span, the AFC window keeps its 3.2 lines
 * (half a native window held 1.6, so line-timing evidence came and went).
 * Exact 2:1 selection preserves the established AFC's time/frequency
 * geometry. This never feeds or paces the DAC path. IQ48 has no integer
 * mapping to IQ40 and must not use that estimator. */
static inline void rx_rate_decimate2(uint8_t *dst, const uint8_t *src, size_t samples)
{
    if (!((uintptr_t)src & 3u))
        for (;samples>=2u;samples-=2u,src+=4) {
            uint32_t w;
            memcpy(&w,src,4);   /* one aligned load: the copy runs under the ring-lap deadline */
            *dst++=(uint8_t)w;
            *dst++=(uint8_t)(w>>16);
        }
    for (;samples;--samples,src+=2) *dst++=*src;
}
