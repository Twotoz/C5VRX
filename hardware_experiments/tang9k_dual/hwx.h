/* C5VRX by Twotoz and the C5VRX contributors: hardware experiment TANG9K_DUAL.
 *
 * Built only with -DC5VRX_HWX=tang9k_dual (CI job build-hwx); the production
 * firmware never includes this file. Two XIAO ESP32-C5 boards with this
 * build feed one Sipeed Tang Nano 9K (GW1NR-9), which combines both IQ
 * streams (pre-detection MRC), demodulates and drives the output (six-bit
 * DAC, 40-pin RGB LCD or HDMI; see fpga/rtl/top.v).
 * Extends the FPGA_IQ experiment (raw MODEM_DIAG Q4/I4 to an FPGA), the
 * PR #257 / LAP87 clock export (issue #256) and the dual-C5 diversity notes
 * in legacy/c5vrx3/docs/range-v2-research-notes.md.
 *
 * Source format/tap: MODEM_DIAG ULTRAFINE lane {9,6,5,4} per component,
 * GPIO matrix straight to front-header pads. The PARLIO RX sample clock
 * (PLL_F240M/6, 40 MHz; the clock the receiver itself samples IQ with) is
 * exported on D10: the FPGA captures every C5 on its own clock. The
 * receiver (gain, DC, AFC, calibration) keeps running on its RX40/raw32K
 * loopback of the same pads. No DAC on the XIAO: PARLIO TX drives no pads
 * except the menu (below). GPIO11 is not used (LAP87: bit 2 on GPIO11
 * rose one TX clock late in 5-8% of pairs).
 *
 *   iq[n]  signal   XIAO pad  GPIO  C5 A -> Tang pin   C5 B -> Tang pin
 *   0      Q bit 0  D0        1     38                 29
 *   1      Q bit 1  D1        0     37                 30
 *   2      Q bit 2  D2        25    36                 77
 *   3      Q sign   D3        7     39                 76
 *   4      I bit 0  D4        23    25                 48
 *   5      I bit 1  D5        24    26                 49
 *   6      I bit 2  D7        12    27                 31
 *   7      I sign   D8        8     28                 32
 *   clk    RX40     D10       10    63 (RPLL_T_IN)     79 via 680R series,
 *                                                      820R to GND (bank 3
 *                                                      is 1.8 V)
 *   link   UART RX  D9        9     channel link from the FPGA, one wire
 *                                     to both C5s (see below)
 *   GND    GND      GND       -     GND                GND
 *
 * All other Tang pins above are bank 1/2 (3.3 V LVCMOS) header pins that
 * the LCD (33-35, 40-42, 51, 53-57, 68-75) and HDMI (68-75, AC-coupled and
 * pulled to 3.3 V through 49.9R on the connector side) do not use, so one
 * wiring serves every output. C5 A is the timing master of the FPGA.
 *
 * STRONG LANE and IQ LANE are held on ultrafine (HWX_FIXED_LANE): a lane
 * switch would change the meaning of the bits under the FPGA.
 *
 * Menu on the IQ pads (HWX_MENU_ON_IQ, as FPGA_IQ): while the menu or a
 * status screen is shown and nothing measures IQ, iq[5:0] carry the menu
 * raster's six-bit composite codes at TX40 and iq[7:6] = 0; raw IQ never
 * holds I sign and I bit 2 at 0 for a whole line (64 us). The FPGA passes
 * that raster to its output. Data and clock pause during receiver
 * restarts; the FPGA must reacquire. Common ground, no pull-ups/downs
 * (GPIO7/25 are strapping pads).
 *
 * Channel link (HWX_CHANNEL_RX_GPIO, main/channel_link.h): the FPGA owns the
 * channel. It repeats a four-byte frame (0xC5, 'C', index, 0x79 ^ index) at
 * 115200 8N1 on one wire to D9 of both C5s; a C5 on another channel retunes
 * through the menu's channel path (saved, flight-logged). A channel chosen
 * on a C5 itself is therefore overridden while the FPGA sends. The line
 * stays silent until the FPGA's channel button is first pressed. On the
 * Tang Nano 9K the link leaves bank 3 (1.8 V) through one NPN: 1k into the
 * base from pin 80, emitter to GND, collector to the link wire with 4.7k to
 * 3.3 V (the FPGA sends inverted). */
#pragma once

#define HWX_NAME "TANG9K_DUAL"
/* Shared MODEM_DIAG output / PARLIO RX input order: Q low..high, I low..high. */
#define HWX_IQ_GPIO_NUMS {1, 0, 25, 7, 23, 24, 12, 8}
/* PARLIO RX sample clock output (D10). */
#define HWX_RX_CLK_GPIO 10
/* No resistor DAC: PARLIO TX drives no pads and Clock Pin D10 is refused. */
#define HWX_NO_DAC 1
/* The menu raster on the IQ pads while the menu is shown (see above). */
#define HWX_MENU_ON_IQ 1
/* STRONG LANE OFF and IQ LANE ULTRA, not selectable. */
#define HWX_FIXED_LANE 1
/* Channel link from the FPGA (main/channel_link.h): UART RX on D9. */
#define HWX_CHANNEL_RX_GPIO 9
