#pragma once
/* C5VRX-5 by Twotoz and the C5VRX contributors: OSD key bench, step 1
 * (osd_key.c, osd_key_logic.h). All calls are no-ops until console O arms
 * it; the key only ever runs while the menu/status raster owns TX. */

void osd_key_prime(void);          /* transport_start_chain, before PARLIO TX starts */
void osd_key_go(void);             /* same, interrupts masked, right after it started */
void osd_key_stop(void);           /* every chain stop: pin low, I2S and GDMA stopped */
void osd_key_command(int c);       /* control task: O < > , . + - */
void osd_key_print(void);
