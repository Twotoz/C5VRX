#pragma once
/* C5VRX-5 by Twotoz and the C5VRX contributors: XIAO ESP32-C5 pin map.
 * The edge has only D0..D10 (+5V, GND, 3V3). DAC: D4..D9 (transport.c, do
 * not move). IQ bits are MODEM_DIAG outputs read back by PARLIO RX through
 * the GPIO matrix; they need no wiring, so they may sit on GPIOs that are
 * not on the edge. Q bits 0..3, I bits 4..7, LSB first:
 * - Q0 on GPIO27 (the active-low user LED, which glows faintly) frees
 *   header D0 for the external menu button (control.c);
 * - I0 on GPIO10 (header D10), or on GPIO2 when the OSD key bench is built
 *   (CONFIG_C5VRX_OSD_KEY), which then uses D10. */
#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#endif

#if defined(CONFIG_C5VRX_OSD_KEY) && CONFIG_C5VRX_OSD_KEY
#define C5VRX_OSD_KEY 1
#else
#define C5VRX_OSD_KEY 0
#endif

#define C5VRX_OSD_KEY_GPIO     10      /* header D10, only with C5VRX_OSD_KEY */
#define C5VRX_EXT_BUTTON_GPIO  1       /* header D0 to GND, internal pull-up */
#define C5VRX_IQ_I0_GPIO       (C5VRX_OSD_KEY ? 2 : 10)
#define C5VRX_IQ_PINS          {27, 0, 25, 7, C5VRX_IQ_I0_GPIO, 5, 3, 4}
