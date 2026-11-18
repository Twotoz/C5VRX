/* C5VRX by Twotoz and the C5VRX contributors: hardware experiment TRIPLEVERSITY.
 *
 * Built only with -DC5VRX_HWX=tripleversity (CI job build-hwx); the
 * production firmware never includes this file. Three XIAO ESP32-C5 boards
 * with this build feed one Sipeed Tang Nano 20K (GW2AR-18), which aligns
 * them, combines the three IQ streams (pre-detection MRC, two phase
 * trackers) and demodulates (hardware_experiments/tang9k_dual/fpga:
 * rtl/top3_20k.v, rtl/mrc3_core.v, model/mrc3_model.py). Extends
 * TANG9K_DUAL (same firmware hooks, same pads) from two C5s with 4-bit I/Q
 * to three C5s with 3-bit I/Q (host model fpga/model/eval3.py: 3 x 3 bit
 * holds lock and SINAD better than 2 x 4 bit under fading).
 *
 * Only the top three bits of each component go to the FPGA: sign, bit 2 and
 * bit 1 of the C5's 4-bit MODEM_DIAG lane. STRONG LANE and IQ LANE stay
 * selectable (no HWX_FIXED_LANE): a lane switch scales the bits, the
 * combiner only uses the angle and re-weights every 3.2 us.
 *
 *   iq[n]  signal   XIAO pad  GPIO  Tang Nano 20K pin  C5 A  C5 B  C5 C
 *   1      Q bit 1  D1        0     iq_x[0]            73    72    15
 *   2      Q bit 2  D2        25    iq_x[1]            74    71    16
 *   3      Q sign   D3        7     iq_x[2]            75    53    17
 *   5      I bit 1  D5        24    iq_x[3]            85    54    18
 *   6      I bit 2  D7        12    iq_x[4]            86    55    19
 *   7      I sign   D8        8     iq_x[5]            56    51    20
 *   clk    RX40     D10       10    clk_x              79    76    80
 *   link   UART RX  D9        9     link_tx 52, one wire to all three C5s
 *   GND    GND      GND       -     GND
 * iq[0] (D0) and iq[4] (D4) stay unconnected. All Tang pins are 3.3 V
 * header pins the 40-pin LCD leaves free (fpga/tang_nano_20k.cst); pins
 * 15-20 also drive the 20K's onboard LEDs, which flicker with C5 C's IQ.
 * C5 A is the timing master of the FPGA.
 *
 * Menu: as TANG9K_DUAL the C5 menu raster replaces IQ on the pads while it
 * is shown (HWX_MENU_ON_IQ); the FPGA does not pass it through (it gets its
 * own OSD). Channel link: as TANG9K_DUAL (main/channel_link.h), 3.3 V, not
 * inverted, no transistor. Common ground, no pull-ups/downs (GPIO7/25 are
 * strapping pads). */
#pragma once

#define HWX_NAME "TRIPLEVERSITY"
/* Shared MODEM_DIAG output / PARLIO RX input order: Q low..high, I low..high. */
#define HWX_IQ_GPIO_NUMS {1, 0, 25, 7, 23, 24, 12, 8}
/* PARLIO RX sample clock output (D10). */
#define HWX_RX_CLK_GPIO 10
/* No resistor DAC: PARLIO TX drives no pads and Clock Pin D10 is refused. */
#define HWX_NO_DAC 1
/* The menu raster on the IQ pads while the menu is shown. */
#define HWX_MENU_ON_IQ 1
/* Channel link from the FPGA (main/channel_link.h): UART RX on D9. */
#define HWX_CHANNEL_RX_GPIO 9
