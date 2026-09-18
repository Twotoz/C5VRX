#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#include "grab.h"

/**
 * uart_link.c - frames from the grabber to a display board over UART
 * (CONFIG_C5VRX_LINK_MODE). Step 3 of the T-Embed display link.
 *
 * Packets, all little endian, CRC-8 (poly 0x07, init 0) over type..payload:
 *
 *   A5 5A | type | frame | row | payload | crc8
 *
 *   type 0x10  info, row = 0xff, 16 bytes:
 *              [0] protocol version (1)   [1] rows captured
 *              [2] flags: bit0 PAL, bit1 locked, bit2 4-bit rows follow
 *              [3..4] frequency MHz       [5] RF gain index
 *              [6] error: 0 none, 1 no horizontal sync, 2 no vertical
 *                  interval, 3 lost sync, 4 timeout, 5 other
 *              [7] fields used            [8..9] grab time ms
 *              [10..11] line period, ns - 60000    [12..15] 0
 *   type 0x01  row, GRAB_W bytes of 8-bit luma (0 blanking .. 255 white)
 *   type 0x02  row, GRAB_W / 2 bytes, two 4-bit pixels per byte, left pixel
 *              in the low nibble
 *
 * Each frame is one info packet followed by its GRAB_H rows (none when the
 * grab failed). The receiver resynchronises on the magic after any error.
 */

#define UART_LINK_MAGIC0   0xA5u
#define UART_LINK_MAGIC1   0x5Au
#define UART_LINK_T_INFO   0x10u
#define UART_LINK_T_ROW8   0x01u
#define UART_LINK_T_ROW4   0x02u
#define UART_LINK_INFO_LEN 16u

esp_err_t uart_link_init(void);

/** Queue one frame (info + rows) on the UART. Blocks while the TX buffer is
 * full, which paces the caller to the line rate. */
void uart_link_send_frame(const uint8_t *img, int rows, const grab_info_t *info,
                          uint8_t frame_id, bool pack4);
