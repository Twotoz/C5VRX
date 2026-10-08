#pragma once
/* Self-fitting EDGE range demod (C5VRX by Twotoz/contributors).
 * Re-synthesizes the shared-word LUT16 for the measured VTX deviation and
 * carrier centre, bit-exact with tools/dsp_search/edge_fsm.py (clip
 * detector, freq/advance/avg output, uniform grid, PAIR4411 tokens). Token bits
 * 13..15 stay those of the loaded program; this fills bits 0..12. */
#include <stdbool.h>
#include <stdint.h>

enum { EDGE_AF_OUT_FREQ = 0, EDGE_AF_OUT_ADVANCE = 1, EDGE_AF_OUT_AVG = 2 };
enum { EDGE_AF_DET_CLIP = 0, EDGE_AF_DET_TANH = 1, EDGE_AF_DET_SINE = 2, EDGE_AF_DET_SOFTHOLD = 3 };
typedef struct {
    uint8_t phases, frequencies;        /* phases * frequencies * 8 == 1024 */
    uint8_t output;                     /* EDGE_AF_OUT_* (edge_fsm output block) */
    uint8_t detector;                   /* EDGE_AF_DET_* */
    bool reliability;                   /* per-token weights from the pinned decoder */
    double kp, ki, hold, limit, mix, low_hz, high_hz;
} edge_af_params_t;

/* Nominal FM transfer of the search model: f = centre + dev * (ire - 30) * 47.857 kHz. */
#define EDGE_AF_KHZ_PER_IRE 47.857142857142854
#define EDGE_AF_SYNC_TO_BLANK_KHZ (40.0 * EDGE_AF_KHZ_PER_IRE)    /* 1914 kHz at dev 1 */
#define EDGE_AF_BLANK_TO_CENTRE_KHZ (30.0 * EDGE_AF_KHZ_PER_IRE)  /* 1436 kHz at dev 1 */

void edge_af_pinned(edge_af_params_t *p);
/* Returns false for an unsupported allocation or fit. */
bool edge_af_synthesize(const edge_af_params_t *p, double deviation, double centre_hz, uint16_t low13[1024]);
/* Fit from the AFC sync-tip and porch frequencies (kHz, tuned-relative). */
bool edge_af_fit(int sync_khz, int porch_khz, double *deviation, double *centre_hz);
