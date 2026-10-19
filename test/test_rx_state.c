/* C5VRX-5 by Twotoz and the C5VRX contributors: host test of the receiver
 * state supervisor in shadow mode (main/rx_state.h).
 *
 *   cc -O2 -std=c11 -I../main test_rx_state.c -o t && ./t
 */
#include <stdio.h>
#include "rx_state.h"

static unsigned s_fail, s_run;
static void check(const char *name, int ok)
{
    ++s_run;
    printf("%-4s %s\n", ok ? "ok" : "FAIL", name);
    s_fail += !ok;
}

static rx_obs_t picture(int cn_x10) { return (rx_obs_t){.video_pct = 95, .cn_x10 = (int16_t)cn_x10, .vq_known = true, .vq_snr_x10 = 90}; }
static rx_obs_t nothing(void) { return (rx_obs_t){.video_pct = 0, .cn_x10 = RX_CN_UNKNOWN}; }
static uint8_t run(rx_state_t *s, rx_obs_t o, unsigned n) { uint8_t st = 0; while (n--) st = rx_state_step(s, &o); return st; }

int main(void)
{
    rx_state_t s;
    rx_state_init(&s);
    check("no VTX: ACQUIRE, reason no_carrier", run(&s, nothing(), 5) == RX_ACQUIRE && s.reason == RXR_NO_CARRIER);
    check("one good second does not jump to TRACK (dwell)", run(&s, picture(150), 1) == RX_ACQUIRE);
    check("two agreeing seconds: TRACK", run(&s, picture(150), 1) == RX_TRACK && s.reason == RXR_NONE);
    check("a weak carrier with a picture: EDGE after the dwell", run(&s, picture(50), 2) == RX_EDGE && s.reason == RXR_WEAK);
    check("picture lost: RECOVER at once", run(&s, nothing(), 1) == RX_RECOVER);
    check("RECOVER ends in ACQUIRE after 10 s without a picture", run(&s, nothing(), 10) == RX_ACQUIRE);
    rx_state_init(&s);
    (void)run(&s, picture(150), 3);
    rx_obs_t o = picture(150);
    o.fault = true;
    check("FAULT at once", run(&s, o, 1) == RX_FAULT && s.reason == RXR_FAULT);
    o.fault = false; o.owned = true;
    check("menu/scan/calibration: OWNED at once", run(&s, o, 1) == RX_OWNED && s.reason == RXR_OWNED);
    check("back from OWNED at once", run(&s, picture(150), 1) == RX_TRACK);
    o = picture(150); o.one_sided_clips = 40;
    check("one-sided clipping names DC, not overload", run(&s, o, 1) == RX_TRACK && s.reason == RXR_DC_CLIPPING);
    o.overloads = 3;
    check("two-sided overload outranks DC clipping", ((void)run(&s, o, 1), s.reason == RXR_OVERLOAD));
    o = picture(150); o.dc_mcells = 1500;
    check("a large DC residual is named", ((void)run(&s, o, 1), s.reason == RXR_DC_RESIDUAL));
    o = nothing(); o.cn_x10 = 120;
    check("sync lost with strong RF: a demodulation problem, not range", ((void)run(&s, o, 1),
          s.reason == RXR_SYNC_LOSS_WITH_RF));
    rx_state_init(&s);
    (void)run(&s, picture(150), 3);
    o = picture(150); o.vq_snr_x10 = 10;
    check("low picture SNR at good C/N: EDGE, reason low_picture_snr", run(&s, o, 2) == RX_EDGE &&
          s.reason == RXR_LOW_PICTURE_SNR);
    o = picture(150); o.vq_known = false; o.cn_x10 = RX_CN_UNKNOWN;
    check("unknown evidence is no impairment: TRACK, reason none", run(&s, o, 2) == RX_TRACK && s.reason == RXR_NONE);
    rx_state_init(&s);
    (void)run(&s, picture(150), 3);
    for (unsigned k = 0; k < 20; ++k) { rx_obs_t a = picture(k & 1 ? 150 : 50); (void)rx_state_step(&s, &a); }
    check("C/N flickering around the line does not toggle TRACK/EDGE every second", s.state == RX_TRACK);
    check("state byte packs state and reason", rx_state_byte(&(rx_state_t){.state = RX_EDGE, .reason = RXR_WEAK}) ==
          ((RX_EDGE + 1) | RXR_WEAK << 3));
    printf("%u/%u rx state checks passed\n", s_run - s_fail, s_run);
    return s_fail != 0;
}
