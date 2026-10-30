/* C5VRX by Twotoz/contributors: explicit acquired-IQ rate. */
#pragma once
#include <stdint.h>
#include <stddef.h>
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

/* Fixed-IQ40 observer view only, after the native-rate picture observer.
 * Exact 2:1 selection at IQ80 preserves the established AFC's time/frequency
 * geometry without another buffer. This never feeds or paces the DAC path.
 * IQ48 has no integer mapping to IQ40 and must not use that estimator. */
static inline size_t rx_rate_observer40(uint8_t *raw, size_t n, uint32_t hz)
{
    if (hz==40000000u) return n;
    if (hz!=80000000u) return 0;
    for (size_t k=0;k<n/2u;++k) raw[k]=raw[2u*k];
    return n/2u;
}
