#pragma once

#include <stdbool.h>

#include "esp_err.h"

/**
 * Link mode (CONFIG_C5VRX_LINK_MODE): the BitScrambler runs the Phase5 FM
 * demodulator in loopback over snapshots of the raw I/Q ring instead of
 * feeding PARLIO TX. Step 1 of the T-Embed display link: prove that a
 * snapshot demodulates to composite video with visible line syncs.
 */

/** Create the loopback BitScrambler and load fm_loop.bsasm. Call after
 * video_start() has started the RX ring. */
esp_err_t link_init(void);

/** Console 'v': copy the most recent completed ring descriptors (time
 * ordered, ~300 us), demodulate them through the loopback BitScrambler and
 * print both buffers as hex blocks for tools/link_dump.py. */
void link_dump(void);

/** True while link_dump() is printing: other tasks must keep the console
 * quiet so the hex blocks reach the host intact. */
bool link_dump_active(void);

/** Console 'g': grab one frame (grab.c) and print it as hex rows for
 * tools/link_frame.py. */
void link_grab(void);

/** Console 'S': start / stop streaming frames over the UART link. */
void link_stream_toggle(void);

/** Console 'P': switch the UART link between 8-bit and 4-bit rows. */
void link_pack_toggle(void);
