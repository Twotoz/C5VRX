/* C5VRX by Twotoz/contributors: bounded native GPIO2 edge witness.
 * Timestamps are ISR clock reads, NOT hardware edge captures. No filter,
 * latency, camera-sync, or monitor-lock guarantee follows from a candidate. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#define SNW_EDGE_LIMIT 8u
typedef enum {
    SNW_IDLE,SNW_ARMED,SNW_COMPLETE,SNW_EXPIRED,SNW_RATE_FAULT,SNW_CLOCK_FAULT
} snw_status_t;
typedef struct {
    snw_status_t status;
    uint64_t expected,falling,rising,last_tick;
    uint32_t low_width,edges,dropped,windows,completed;
    bool have_falling,completed_low,rate_fault,clock_fault;
} snw_snapshot_t;

/* All public functions are task-context, exclusive-owner calls. Begin AFTER
 * sync_edge_clock is prepared/armed and after GPIO2's mirror route is fixed.
 * Refuse any existing GPIO interrupt configuration/ownership. GPIO2 IOMUX,
 * matrix and data routing are never changed; owner must keep them unchanged.
 * The current clock-read call chain is flash resident, so the interrupt is
 * deliberately allocated WITHOUT ESP_INTR_FLAG_IRAM. Cache-off latency is
 * therefore unbounded here. State is allocated only by begin after startup;
 * no unused ISR code is placed in scarce IRAM until that full chain is proven. */
bool sync_native_witness_begin(void);

/* Open only near the expected falling edge; no automatic opening timer is
 * allocated. Each explicit arm permits <=8 ISR observations. Rate/clock faults
 * latch until end/begin. Snapshot or disable_window closes a quiet expired
 * window; production alarm integration must call one at the deadline. */
bool sync_native_witness_enable_window(uint64_t expected_fall,
    uint32_t early_ticks,uint32_t late_ticks,uint32_t min_low_ticks,uint32_t max_low_ticks);
bool sync_native_witness_disable_window(void);
bool sync_native_witness_snapshot(snw_snapshot_t *out);
bool sync_native_witness_end(void);
