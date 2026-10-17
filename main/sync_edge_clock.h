/* C5VRX by Twotoz/contributors: experimental native-edge clock, task context.
 * Caller holds GPIO2 driven quiet/LOW during prepare and arm.
 * Arm RMT with its private filter OFF, then arm(), emit a private marker,
 * enable_filter(), and only then connect the native mirror. The first
 * marker edge reaches both consumers before the variable filter is enabled.
 * No camera or phase-accuracy qualification is implied by a running timer. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
bool sync_edge_clock_prepare(void);
bool sync_edge_clock_arm(void);
bool sync_edge_clock_enable_filter(void);
bool sync_edge_clock_disable_filter(void);
bool sync_edge_clock_read(uint64_t *ticks);
bool sync_edge_clock_close(void);
uint32_t sync_edge_clock_filter_ns(void);
const char *sync_edge_clock_error(void);

/* Single alarm consumer on the SAME clock as native observation. Callback
 * must be IRAM/cache safe, bounded, and return whether to yield from ISR.
 * Binding/unbinding is task context, serialized with close. at/off can be
 * called from that alarm ISR. Unbind disarms before releasing callback data. */
typedef bool (*sync_edge_alarm_fn)(uint64_t count,void *arg);
bool sync_edge_clock_alarm_bind(sync_edge_alarm_fn callback,void *arg);
bool sync_edge_clock_alarm_at(uint64_t ticks);
bool sync_edge_clock_alarm_off(void);
