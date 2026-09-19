#pragma once

#include <stdbool.h>

#include "esp_err.h"

/**
 * Link mode (CONFIG_C5VRX_LINK_MODE): no analog output. The receiver grabs
 * frames in software (grab.c) and streams them to a display board over UART
 * (link_tx.c, uart_link.c; protocol in link_proto.h); the BitScrambler is
 * free for loopback demodulation of ring snapshots dumped to the host.
 */

/** Create the loopback BitScrambler, the grabber, the UART link and the
 * stream task. Call after video_start() has started the RX ring. */
esp_err_t link_init(void);

/** Console 'v': dump a raw ring snapshot and its BitScrambler loopback
 * demodulation as hex blocks for tools/link_dump.py. */
void link_dump(void);

/** True while a console dump is printing: other tasks keep the console quiet
 * so the hex blocks reach the host intact. */
bool link_dump_active(void);

/** Console 'g': grab one frame and print it as hex rows for
 * tools/link_frame.py. */
void link_grab(void);

/** Console 'S': start / stop streaming over the UART link. */
void link_stream_toggle(void);

/** Console 'P': cycle the picture mode (auto, fine, raw; see link_proto.h). */
void link_mode_cycle(void);

/** Console 'N': start / stop the channel scan. */
void link_scan_toggle(void);

/** Console 'M': horizontal smoothing on / off. */
void link_smoothing_toggle(void);
