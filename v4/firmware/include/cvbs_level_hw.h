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
/* AutoFit LUT: written only while the engine is halted between program
 * load and start (c5v4_fit_window). EDGE words are self-tested against the
 * pinned synthesis at load; a failed write restores the pristine table. */
enum { C5V4_FIT_NONE = 0, C5V4_FIT_EDGE = 1, C5V4_FIT_PAIR = 2 };
void c5v4_fit_set_program(int kind);
void c5v4_fit_window(bool open);
int c5v4_fit_ready(void);
const uint16_t *c5v4_fit_pristine(void);
/* Pristine words of a program kind once self-tested this boot, else NULL. */
const uint16_t *c5v4_fit_pristine_for(int kind);
bool c5v4_fit_write_stopped(const uint16_t words[1024]);
void c5v4_fit_print(void);
bool c5v4_decoder_recenter(int di_mcells, int dq_mcells);
void c5v4_decoder_dc(int dc[2]);
void c5v4_decoder_print(void);
