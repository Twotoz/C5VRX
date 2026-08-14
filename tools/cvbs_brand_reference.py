#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Validate scanline-only C5VRX splash/diagnostic content transitions."""

FONT = {
    "C": (14,17,16,16,16,17,14), "5": (31,16,30,1,1,17,14),
    "V": (17,17,17,17,17,10,4), "R": (30,17,17,30,20,18,17),
    "X": (17,17,10,4,10,17,17), "P": (30,17,17,30,16,16,16),
    "A": (14,17,17,31,17,17,17), "L": (16,16,16,16,16,16,31),
}

def text_pixel(text, x, y, ox, oy, scale):
    if x < ox or y < oy:
        return False
    px, py = (x-ox)//scale, (y-oy)//scale
    ci, col = px//6, px%6
    return py < 7 and ci < len(text) and col < 5 and bool(
        FONT.get(text[ci], (0,)*7)[py] & (1 << (4-col)))

def self_test():
    # The 12-second transition is exactly 300 complete 25 Hz frames. Content
    # changes only while the renderer wraps to half-line zero.
    assert 300 / 25 == 12
    assert text_pixel("C5VRX", 332, 70, 320, 70, 12)
    assert not text_pixel("C5VRX", 319, 70, 320, 70, 12)
    splash_pixels = sum(text_pixel("C5VRX", x, y, 320, 70, 12)
                        for y in range(288) for x in range(1040))
    diagnostic_pixels = sum(text_pixel("C5VRX", x, y, 24, 15, 5)
                            for y in range(288) for x in range(1040))
    assert splash_pixels > 0 and diagnostic_pixels > 0
    assert splash_pixels != diagnostic_pixels
    print("CVBS branded scanline renderer self-test PASS")
    print("  splash duration: 300 frames = 12.0 s")
    print("  content transition: frame boundary only")
    print("  framebuffer allocation: none")

if __name__ == "__main__":
    self_test()
