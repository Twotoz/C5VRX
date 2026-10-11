/* C5VRX by Twotoz/contributors: local integer IQ-clock research prototype.
 * GPL-3.0-only; https://github.com/Twotoz/C5VRX
 * No firmware integration, DMA ownership or physical CPU timing acceptance.
 * Two128-bin hypotheses; input chunks MUST be contiguous and correctly tagged.
 * Feed chunks of at most80000 bytes; accumulate at most1270 pairs per window
 * (2ms at stride63 or4ms at stride127). The observer's scoring,
 * period adaptation, gap recovery and transport are not implemented here.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

#ifndef IQ_CLOCK_STRIDE
#define IQ_CLOCK_STRIDE 63
#endif
#if IQ_CLOCK_STRIDE == 63
#define IQ_PAL_INCREMENT 105696461u
#define IQ_NTSC_INCREMENT 106435597u
#elif IQ_CLOCK_STRIDE == 127
#define IQ_PAL_INCREMENT 213070643u
#define IQ_NTSC_INCREMENT 214560648u
#else
#error Unsupported research stride
#endif

typedef struct {
    int32_t hist[2][3][128];
    int32_t sums[4];
    uint32_t phase[2];
    int32_t next_offset;
    uint8_t pending_a;
    bool pending;
} iq_clock_stream_t;

static inline int32_t odd_nibble(unsigned n)
{
    return 2 * (int32_t)(n & 7) - 2 * (int32_t)(n & 8) + 1;
}

void iq_clock_stream_init(iq_clock_stream_t *s)
{
    memset(s, 0, sizeof(*s));
    s->phase[0] = 838861u;
    s->phase[1] = 844727u;
}

static inline bool iq_clock_pair(iq_clock_stream_t *s, uint8_t a, uint8_t b)
{
    if (s->sums[3] >= 1270) return false;
    const int32_t ia = odd_nibble(a >> 4), qa = odd_nibble(a & 15);
    const int32_t ib = odd_nibble(b >> 4), qb = odd_nibble(b & 15);
    const int32_t re = ib * ia + qb * qa;
    const int32_t im = qb * ia - ib * qa;
    s->sums[0] += re;
    s->sums[1] += im;
    s->sums[2] += ia * ia + qa * qa;
    ++s->sums[3];
    const uint32_t increment[2] = {IQ_PAL_INCREMENT, IQ_NTSC_INCREMENT};
    for (unsigned t = 0; t < 2; ++t) {
        const uint32_t p = s->phase[t];
        const unsigned bin0 = p >> 25, bin1 = (bin0 + 1) & 127;
        const int32_t w1 = (p >> 17) & 255, w0 = 256 - w1;
        s->hist[t][0][bin0] += re * w0;
        s->hist[t][0][bin1] += re * w1;
        s->hist[t][1][bin0] += im * w0;
        s->hist[t][1][bin1] += im * w1;
        s->hist[t][2][bin0] += w0;
        s->hist[t][2][bin1] += w1;
        s->phase[t] += increment[t];
    }
    return true;
}

bool iq_clock_stream_feed(iq_clock_stream_t *s, const uint8_t *raw, size_t n)
{
    if (n > 80000 || (!raw && n)) return false;
    if (!n) return true;
    int32_t k = s->next_offset;
    if (s->pending) {
        if (!iq_clock_pair(s, s->pending_a, raw[0])) return false;
        s->pending = false;
        k += IQ_CLOCK_STRIDE;
    }
    while (k + 1 < (int32_t)n) {
        if (!iq_clock_pair(s, raw[k], raw[k + 1])) return false;
        k += IQ_CLOCK_STRIDE;
    }
    if (k < (int32_t)n) {
        s->pending_a = raw[k];
        s->pending = true;
    }
    s->next_offset = k - (int32_t)n;
    return true;
}

#ifdef IQ_CLOCK_HOST_PROOF
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv)
{
    static uint8_t raw[80000];
    iq_clock_stream_t state;
    iq_clock_stream_init(&state);
    const size_t n = fread(raw, 1, sizeof(raw), stdin);
    const size_t chunk = argc > 1 ? (size_t)strtoul(argv[1], NULL, 10) : 4092;
    if (!chunk || chunk > 80000 || n != 80000) return 2;
    for (size_t start = 0; start < n; start += chunk) {
        const size_t take = n - start < chunk ? n - start : chunk;
        if (!iq_clock_stream_feed(&state, raw + start, take)) return 3;
    }
    if (fwrite(state.hist, 1, sizeof(state.hist), stdout) != sizeof(state.hist)) return 4;
    if (fwrite(state.sums, 1, sizeof(state.sums), stdout) != sizeof(state.sums)) return 4;
    if (fwrite(state.phase, 1, sizeof(state.phase), stdout) != sizeof(state.phase)) return 4;
    return 0;
}
#endif
