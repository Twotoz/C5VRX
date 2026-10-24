/* C5VRX by Twotoz and contributors: build-time GPIO2 ownership. */
#pragma once
#include <stdbool.h>

/* True even while TX is stopped: observers must never borrow the pad. */
bool transport_sample_clock_enabled(void);
