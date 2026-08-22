/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_preview_geometry.h"

/* Ladder rungs: aspect-consistent 4:3 steps from the proof floor to the
 * Full-Speed-USB ceiling (issue #5 comment 1). */
static const struct {
    uint16_t width;
    uint16_t height;
} k_ladder[] = {
    {160, 120}, {176, 132}, {192, 144}, {224, 168}, {256, 192}, {320, 240},
};

bool c5vrx_preview_size_valid(const uint16_t width, const uint16_t height)
{
    for (unsigned i = 0; i < sizeof(k_ladder) / sizeof(k_ladder[0]); ++i) {
        if (k_ladder[i].width == width && k_ladder[i].height == height)
            return true;
    }
    return false;
}

uint16_t c5vrx_preview_row_for_active_line(const uint16_t active_line,
                                           const uint16_t active_lines,
                                           const uint16_t height)
{
    if (!active_lines || !height || active_line >= active_lines) return 0;
    return (uint16_t)((uint32_t)active_line * height / active_lines);
}

uint16_t c5vrx_preview_col_for_sample(const uint32_t sample_in_line,
                                      const uint32_t active_start,
                                      const uint32_t active_samples,
                                      const uint16_t width)
{
    if (!width || !active_samples || sample_in_line < active_start)
        return width;
    const uint32_t into = sample_in_line - active_start;
    if (into >= active_samples) return width;
    return (uint16_t)(into * width / active_samples);
}
