/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* USB preview resolution ladder geometry (issue #5 comment 1).
 *
 * Resolution is defined by real transported samples, never by host
 * upscaling: W maps onto the measured active-line span, H maps onto the
 * active lines of ONE field (bob-style, field-native), so PAL ~50 /
 * NTSC ~59.94 temporal updates survive at every rung. The ladder is a
 * whitelist of aspect-consistent rungs benchmarked per issue comment;
 * anything else is rejected instead of silently approximated.
 *
 * Full-Speed USB payload reality (~0.6-0.8 MB/s measured-class):
 *   160x120  19.2 KB/frame -> ~30-40 fps ceiling
 *   176x132  23.2 KB/frame -> ~25-33 fps ceiling
 *   192x144  27.6 KB/frame -> ~21-28 fps ceiling
 *   224x168  37.6 KB/frame -> ~15-21 fps ceiling
 *   256x192  49.2 KB/frame -> ~12-16 fps ceiling
 *   320x240  76.8 KB/frame -> ~7-10 fps ceiling (opt-in)
 * Frame drops under load are counted, never hidden: temporal motion
 * always wins over stale completeness. */

#define C5VRX_PREVIEW_MIN_WIDTH 160u
#define C5VRX_PREVIEW_MAX_WIDTH 320u
#define C5VRX_PREVIEW_MIN_HEIGHT 120u
#define C5VRX_PREVIEW_MAX_HEIGHT 240u

/* True when (width,height) is an exact ladder rung. */
bool c5vrx_preview_size_valid(uint16_t width, uint16_t height);

/* Proportional mappings; inputs are pre-clamped by the caller to the
 * documented active spans. Pure arithmetic so hosts and device agree. */
uint16_t c5vrx_preview_row_for_active_line(uint16_t active_line,
                                           uint16_t active_lines,
                                           uint16_t height);
uint16_t c5vrx_preview_col_for_sample(uint32_t sample_in_line,
                                      uint32_t active_start,
                                      uint32_t active_samples,
                                      uint16_t width);

#ifdef __cplusplus
}
#endif
