/* C5VRX-5 by Twotoz and the C5VRX contributors: onboard flight log
 * (docs/FLIGHT_LOG.md, format flightlog_format.h). Every call is cheap and
 * non-blocking: records go into a small RAM queue that a low-priority task
 * appends to flash every few seconds; a full queue drops (and counts) them. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "diag_logic.h"

void flightlog_start(void);                 /* after settings_load(): partition, resume, session record */
bool flightlog_ready(void);
bool flightlog_dumping(void);              /* console W running: periodic lines hold off */
void flightlog_record(uint8_t type, const void *payload, uint8_t len);
void flightlog_event(uint8_t kind, const void *data, uint8_t len);
/* diag.c close_second: the second's record, its gain range and dropout state. */
void flightlog_second(const diag_record_t *r, uint8_t gain_min, uint8_t gain_max, uint8_t dropout, uint8_t rx_state);
/* diag.c: a dropout trace, record k of count at 50 ms around the onset. */
void flightlog_trace_add(const diag_record_t *r, unsigned k, unsigned count);
void flightlog_snapshot_request(uint8_t trigger);
/* Console: Y status, W dump (text over USB), X twice within 3 s erases. */
void flightlog_console(char key);
