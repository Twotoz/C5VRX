#pragma once
#include "cvbs_monitor.h"
/* prepare must run while demodulator is stopped; observe is a slow control lab. */
void c5v4_level_hw_prepare(void);
bool c5v4_level_hw_ready(void);
void c5v4_level_hw_observe(const c5v4_cvbs_stats_t *, bool, uint32_t, uint64_t);
void c5v4_level_hw_print(void);
void c5v4_level_hw_lock(void);
void c5v4_level_hw_unlock(void);
void c5v4_level_hw_stop(void);
void c5v4_level_hw_invalidate(void);
/* Latch off after a transport fault following live updates, until reboot. */
void c5v4_level_hw_transport_fault(void);
unsigned c5v4_level_hw_period(uint32_t context, uint64_t now_us);
/* Probe result shared with digital DC recentring of the static decoder. */
bool c5v4_level_hw_lut_verified(void);
/* EDGE AutoFit LUT (bits 0..12; program token bits kept). Stopped-engine
 * self-test at program load; live rewrites change only differing words,
 * each with read-back retries; a persistent mismatch blocks further writes. */
bool c5v4_edge_lut_ready(void);
bool c5v4_edge_lut_write(const uint16_t low13[1024]);
void c5v4_edge_lut_print(void);
void c5v4_edge_set_loaded(bool edge);
bool c5v4_decoder_recenter(int di_mcells, int dq_mcells);
void c5v4_decoder_dc(int dc[2]);
void c5v4_decoder_print(void);
