/* SPDX-License-Identifier: GPL-3.0-only */

#include <assert.h>
#include <stdio.h>

#include "c5vrx_preview_geometry.h"

int main(void)
{
    /* Ladder whitelist: rungs accepted, everything else rejected. */
    assert(c5vrx_preview_size_valid(160, 120));
    assert(c5vrx_preview_size_valid(176, 132));
    assert(c5vrx_preview_size_valid(192, 144));
    assert(c5vrx_preview_size_valid(224, 168));
    assert(c5vrx_preview_size_valid(256, 192));
    assert(c5vrx_preview_size_valid(320, 240));
    assert(!c5vrx_preview_size_valid(320, 180));   /* Off-ladder ratio */
    assert(!c5vrx_preview_size_valid(640, 480));   /* Above ceiling */
    assert(!c5vrx_preview_size_valid(0, 0));
    assert(!c5vrx_preview_size_valid(160, 121));

    /* Row mapping is monotonic, in-range and hits both endpoints. */
    for (unsigned h = 120u; h <= 240u; h += 40u) {
        uint16_t prev = 0;
        for (unsigned line = 0; line < 240u; ++line) {
            const uint16_t row = c5vrx_preview_row_for_active_line(
                (uint16_t)line, 240u, (uint16_t)h);
            assert(row < h);
            assert(row >= prev);
            prev = row;
        }
        assert(c5vrx_preview_row_for_active_line(0u, 240u,
                                                 (uint16_t)h) == 0u);
    }

    /* Column mapping spans the full width exactly over active samples
     * and returns width (out-of-frame sentinel) outside them. */
    for (unsigned w = 160u; w <= 320u; w += 40u) {
        assert(c5vrx_preview_col_for_sample(0u, 210u, 1040u,
                                            (uint16_t)w) == w);
        assert(c5vrx_preview_col_for_sample(209u, 210u, 1040u,
                                            (uint16_t)w) == w);
        assert(c5vrx_preview_col_for_sample(1250u, 210u, 1040u,
                                            (uint16_t)w) == w);
        uint16_t prev = 0;
        for (unsigned s = 210u; s <= 1249u; ++s) {
            const uint16_t col = c5vrx_preview_col_for_sample(
                s, 210u, 1040u, (uint16_t)w);
            assert(col < w);
            assert(col >= prev);
            prev = col;
        }
        assert(prev == w - 1u); /* Last active sample reaches last col. */
    }

    puts("preview_geometry_test: PASS");
    return 0;
}
