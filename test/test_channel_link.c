/* C5VRX by Twotoz and the C5VRX contributors: channel link frame parser
 * (main/channel_link.h), host test. */
#include <stdio.h>
#include <stdlib.h>
#include "channel_link.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++fails; } } while (0)

static int feed_all(channel_link_t *p, const uint8_t *b, unsigned n, unsigned channels)
{
    int got = -1;
    for (unsigned k = 0; k < n; ++k) {
        int r = channel_link_feed(p, b[k], channels);
        if (r >= 0) got = r;
    }
    return got;
}

int main(void)
{
    channel_link_t p = {0};
    for (unsigned i = 0; i < 48; ++i) {
        const uint8_t f[4] = {0xC5, 0x43, (uint8_t)i, (uint8_t)(0x79 ^ i)};
        CHECK(feed_all(&p, f, 4, 48) == (int)i);
    }
    const uint8_t bad_chk[4] = {0xC5, 0x43, 7, (uint8_t)(0x79 ^ 8)};
    CHECK(feed_all(&p, bad_chk, 4, 48) == -1);
    const uint8_t out_of_range[4] = {0xC5, 0x43, 48, (uint8_t)(0x79 ^ 48)};
    CHECK(feed_all(&p, out_of_range, 4, 48) == -1);
    /* noise and a repeated sync byte before a frame; frame right after a broken one */
    const uint8_t noisy[] = {0x00, 0xC5, 0xC5, 0x43, 12, 0x79 ^ 12};
    CHECK(feed_all(&p, noisy, sizeof noisy, 48) == 12);
    const uint8_t broken_then_good[] = {0xC5, 0x43, 3, 0xC5, 0x43, 4, 0x79 ^ 4};
    CHECK(feed_all(&p, broken_then_good, sizeof broken_then_good, 48) == 4);
    /* random bytes rarely make a frame: count false frames over 1e6 bytes */
    srand(1);
    unsigned false_frames = 0;
    for (unsigned k = 0; k < 1000000u; ++k)
        if (channel_link_feed(&p, (uint8_t)rand(), 48) >= 0) ++false_frames;
    CHECK(false_frames < 4);
    printf("test_channel_link: %s (%u false frames in 1e6 random bytes)\n", fails ? "FAIL" : "OK", false_frames);
    return fails != 0;
}
