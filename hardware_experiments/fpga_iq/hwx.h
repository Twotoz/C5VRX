/* C5VRX by Twotoz and the C5VRX contributors: hardware experiment FPGA_IQ.
 *
 * Built only with -DC5VRX_HWX=fpga_iq (CI job build-hwx); the production
 * firmware never includes this file. It extends the Clock Pin D10 / FPGA
 * capture work of PR #257 and LAP87's bench report (issue #256): instead of
 * the decoder's six-bit composite codes, the FPGA receives the raw MODEM_DIAG
 * ADC bits and owns demodulation.
 *
 * Source format/tap: MODEM_DIAG ADC bits, GPIO matrix, straight to pads.
 * Acquired rate: the receiver's own PARLIO RX sample clock (40 MHz), exported
 * on HWX_CLOCK_GPIO; capture on its rising edge (RX samples on POS too).
 * CPU vs hardware: no CPU or DMA in the FPGA path; the receiver (gain, DC,
 * AFC, calibration) keeps running on its RX40/raw32K loopback of the same
 * eight IQ pads. No DAC: PARLIO TX keeps running with no pads (menu, video
 * and DAC TEST have no electrical output in this build).
 *
 * Per channel: sign plus six magnitude bits below it. On the ULTRAFINE lane
 * that is ADC bits {9,6,5,4,3,2,1}; the extra bits follow the lane's lowest
 * bit (fine {9,7..2}, coarse {9,8..3}). Bits 3..1 (DIAG 1..3, 11..13) have
 * no board evidence yet; ADC bits 8/7 are not exported (sign extension
 * inside the ultrafine window).
 *
 * Wiring: XIAO ESP32-C5 -> Tang Primer 25K Dock. The eight base bits use
 * PMOD J4 pins 5..12 (operator table 2026-11-16, tang_primer_25k_dock.cst),
 * the pre-2026-10-25 IQ pin order. Extra bits and the clock use the freed
 * DAC pads D4..D9 and MTMS; their FPGA balls are not assigned yet.
 *
 *   iq[n]  signal          XIAO pad       GPIO  J4 pin  ball
 *   0      Q bit 0 (ADC4)  D0             1     5       C11
 *   1      Q bit 1 (ADC5)  D1             0     6       C10
 *   2      Q bit 2 (ADC6)  D2             25    7       B11
 *   3      Q sign  (ADC9)  D3             7     8       B10
 *   4      I bit 0 (ADC4)  D10            10    9       D11
 *   5      I bit 1 (ADC5)  MTDO back pad  5     10      D10
 *   6      I bit 2 (ADC6)  MTDI back pad  3     11      G11
 *   7      I sign  (ADC9)  MTCK back pad  4     12      G10
 *   x0     Q ADC3          D5             24    -       TBD
 *   x1     Q ADC2          D6             11    -       TBD
 *   x2     Q ADC1          D7             12    -       TBD
 *   x3     I ADC3          D8             8     -       TBD
 *   x4     I ADC2          D9             9     -       TBD
 *   x5     I ADC1          MTMS back pad  2     -       TBD
 *   clk    RX sample clock D4             23    -       TBD (clock-capable)
 *
 * 3.3 V LVCMOS inputs on the FPGA, no pull-ups/downs (GPIO2/7/25 are
 * strapping pads), common ground, J4 +3V3 not connected. Clock and data can
 * pause during receiver restarts; the FPGA must reacquire. */
#pragma once

#define HWX_NAME "FPGA_IQ"
/* Shared MODEM_DIAG output / PARLIO RX input order: Q low..high, I low..high. */
#define HWX_IQ_GPIO_NUMS {1, 0, 25, 7, 10, 5, 3, 4}
/* Extra ADC bits, one, two and three below the lane's lowest exported bit. */
#define HWX_EXTRA_BITS 3
#define HWX_EXTRA_Q_GPIOS {24, 11, 12}
#define HWX_EXTRA_I_GPIOS {8, 9, 2}
/* PARLIO RX sample clock output. */
#define HWX_CLOCK_GPIO 23
/* No resistor DAC: PARLIO TX drives no pads and Clock Pin D10 is refused. */
#define HWX_NO_DAC 1
