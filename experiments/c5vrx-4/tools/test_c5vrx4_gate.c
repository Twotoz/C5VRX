/* C5VRX by Twotoz and contributors: native gate must not undo analog LOCK. */
#define _GNU_SOURCE
#include <assert.h>
#include <sys/mman.h>
#include <string.h>
#include <stdio.h>
#include "driver/gptimer.h"
#include "nvs_flash.h"
#include "freertos/semphr.h"
static bool native;
static unsigned timer_calls;
static uint64_t alarm_at;
static StaticSemaphore_t mutex_storage;
#define xSemaphoreCreateRecursiveMutex() xSemaphoreCreateRecursiveMutexStatic(&mutex_storage)
#define ESP_ERR_NO_MEM 4
#define ESP_ERROR_CHECK(e) assert((e)==ESP_OK)
#define portENTER_CRITICAL_ISR(m) portENTER_CRITICAL(m)
#define portEXIT_CRITICAL_ISR(m) portEXIT_CRITICAL(m)
bool rf_native_agc_active(void) { return native; }
esp_err_t gptimer_new_timer(const gptimer_config_t *c, gptimer_handle_t *t)
{ assert(c->resolution_hz==1000000); ++timer_calls; *t=&timer_calls; return ESP_OK; }
esp_err_t gptimer_register_event_callbacks(gptimer_handle_t t,const gptimer_event_callbacks_t *c,void *ctx)
{ (void)t; (void)ctx; assert(c->on_alarm); ++timer_calls; return ESP_OK; }
esp_err_t gptimer_enable(gptimer_handle_t t) { (void)t; ++timer_calls; return ESP_OK; }
esp_err_t gptimer_start(gptimer_handle_t t) { (void)t; ++timer_calls; return ESP_OK; }
esp_err_t gptimer_stop(gptimer_handle_t t) { (void)t; ++timer_calls; return ESP_OK; }
esp_err_t gptimer_set_raw_count(gptimer_handle_t t,uint64_t v)
{ (void)t; assert(v==0); ++timer_calls; return ESP_OK; }
esp_err_t gptimer_set_alarm_action(gptimer_handle_t t,const gptimer_alarm_config_t *a)
{ (void)t; alarm_at=a->alarm_count; ++timer_calls; return ESP_OK; }
uint8_t rf_get_iq_lanes(void) { return 2; }
esp_err_t nvs_open(const char *name,int mode,nvs_handle_t *h)
{ assert(!strcmp(name,"c5vrx4")); (void)mode; *h=1; return ESP_OK; }
esp_err_t nvs_get_u8(nvs_handle_t h,const char *key,uint8_t *v)
{ (void)h; (void)key; (void)v; return ESP_FAIL; }
esp_err_t nvs_set_u8(nvs_handle_t h,const char *key,uint8_t v)
{ (void)h; (void)key; (void)v; return ESP_FAIL; }
esp_err_t nvs_commit(nvs_handle_t h) { (void)h; return ESP_OK; }
void nvs_close(nvs_handle_t h) { (void)h; }
const char *esp_err_to_name(esp_err_t e) { (void)e; return "host"; }
void esp_restart(void) { assert(!"unexpected reboot"); }
void vTaskDelay(unsigned ticks) { (void)ticks; }
static int64_t clock_us = 1000000;
int64_t esp_timer_get_time(void) { return clock_us; }
#include "../pipeline.c"
int main(void)
{
    void *m=mmap((void *)0x600A0000,0x10000,PROT_READ|PROT_WRITE,
                 MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0);
    assert(m!=MAP_FAILED);
    memset(m,0xA5,0x10000);
    unsigned char before[0x10000]; memcpy(before,m,sizeof(before));
    c5vrx4_start(); c5vrx4_suspend(); c5vrx4_suspend();
    c5vrx4_resume(); c5vrx4_resume();
    assert(c5vrx4_console('~')); assert(!c5vrx4_console('T'));
    /* Cover all shared lab keys: the hook passes them to the shared console. */
    const char *keys="{}[]HBWA:LlQ";
    for (const char *k=keys;*k;++k) assert(!c5vrx4_console(*k));
    assert(!timer_calls && !memcmp(before,m,sizeof(before)));
    native=true;
    c5vrx4_start(); assert(s_running && s_open && timer_calls);
    c5vrx4_suspend(); c5vrx4_suspend(); assert(!s_running);
    c5vrx4_resume(); assert(!s_running);
    c5vrx4_resume(); assert(s_running && s_open);
    gptimer_alarm_event_data_t e={.count_value=WINDOW_US,.alarm_value=WINDOW_US};
    gate_alarm(s_timer,&e,NULL);
    assert(!s_open && (AGC_CTRL&AGC_HOLD) && alarm_at==PERIOD_US);
    assert(c5vrx4_console('~') && !s_running && !(AGC_CTRL&AGC_HOLD));
    assert(c5vrx4_console('~') && s_running);

    /* Analog field lock: hold between releases, release only in the VBI. */
    uint64_t epoch = s_epoch_us;
    assert(c5vrx4_native_vbi_wanted());
    uint32_t generation = c5vrx4_native_generation();
    nv_level_t ok = {7, 30, 5, 100, 80}, weak = {2, 10, 0, 300, 60};
    for (unsigned k = 0; k < 4u; ++k)   /* broad pulses every PAL field */
        c5vrx4_native_observe(generation, true, epoch + 50000u + k * 20000u, &ok,
                              epoch + 50100u + k * 20000u);
    assert(s_vbi && !s_vbi_target);
    /* Learn the hardware level, then two weak windows demand one release. */
    uint64_t now = epoch + 120000u;
    for (unsigned k = 0; k < 3u; ++k)
        c5vrx4_native_observe(generation, false, 0, &ok, now + k);
    assert(!s_vbi_target);
    c5vrx4_native_observe(generation, false, 0, &weak, now + 10u);
    assert(!s_vbi_target);
    c5vrx4_native_observe(generation, false, 0, &weak, now + 11u);
    assert(s_vbi_target);
    uint64_t target = s_vbi_target;
    /* 3 ms lead; release lands NV_RELEASE_AFTER_US after the field event. */
    assert(target >= 120011u + VBI_LEAD_US &&
           (target - 50000u) % 20000u >= NV_RELEASE_AFTER_US - 1u &&
           (target - 50000u) % 20000u <= NV_RELEASE_AFTER_US + 1u);
    /* Close the current pace window: VBI mode holds and polls. */
    s_open = true;
    gptimer_alarm_event_data_t c = {.count_value = target - 2500u, .alarm_value = target - 2500u};
    gate_alarm(s_timer, &c, NULL);
    assert(!s_open && (AGC_CTRL & AGC_HOLD) && alarm_at == target - 1500u);
    c.count_value = c.alarm_value = target - 1500u;
    gate_alarm(s_timer, &c, NULL);
    assert(!s_open && (AGC_CTRL & AGC_HOLD) && alarm_at == target - 500u);
    c.count_value = c.alarm_value = target - 500u;
    gate_alarm(s_timer, &c, NULL);
    assert(!s_open && (AGC_CTRL & AGC_HOLD) && alarm_at == target);
    uint32_t releases = s_vbi_releases;
    c.count_value = c.alarm_value = target;
    gate_alarm(s_timer, &c, NULL);
    assert(s_open && !(AGC_CTRL & AGC_HOLD) && alarm_at == target + WINDOW_US &&
           s_vbi_releases == releases + 1u && !s_vbi_target);
    c.count_value = c.alarm_value = target + WINDOW_US;
    gate_alarm(s_timer, &c, NULL);
    assert(!s_open && (AGC_CTRL & AGC_HOLD) && alarm_at == target + WINDOW_US + VBI_POLL_US);
    /* A late interrupt skips the release instead of opening in the picture. */
    s_vbi_target = target + 20000u;
    c.count_value = target + 20000u + VBI_LATE_US + 1u;
    c.alarm_value = target + 20000u;
    gate_alarm(s_timer, &c, NULL);
    assert(!s_open && (AGC_CTRL & AGC_HOLD) && s_vbi_late == 1u && !s_vbi_target);
    /* Severe saturation while held opens at the next poll, outside the VBI. */
    nv_level_t saturated = {40, 105, 300, 0, 90};
    uint64_t later = epoch + target + 40000u;
    for (unsigned k = 0; k < 3u; ++k)
        c5vrx4_native_observe(generation, false, 0, &ok, later + k);
    c5vrx4_native_observe(generation, false, 0, &saturated, later + 10u);
    assert(!s_vbi_urgent);
    c5vrx4_native_observe(generation, false, 0, &saturated, later + 11u);
    assert(s_vbi_urgent);
    c.count_value = c.alarm_value = later - epoch + 500u;
    gate_alarm(s_timer, &c, NULL);
    assert(s_open && !(AGC_CTRL & AGC_HOLD) && !s_vbi_urgent && s_vbi_urgent_releases == 1u);
    c.count_value = c.alarm_value = later - epoch + 500u + WINDOW_US;
    gate_alarm(s_timer, &c, NULL);
    assert(!s_open && (AGC_CTRL & AGC_HOLD));
    /* Lock expiry returns to the periodic pace; | toggles the analog mode. */
    c5vrx4_native_observe(generation, false, 0, &ok, epoch + 2000000u);
    assert(!s_vbi);
    assert(c5vrx4_console('|') && !s_vbi_enabled && !c5vrx4_native_vbi_wanted());
    assert(c5vrx4_native_generation() != generation);
    assert(c5vrx4_console('|') && s_vbi_enabled);
    c5vrx4_suspend();
    munmap(m,0x10000);
    puts("C5VRX-4 Direct Gain LOCK / native gate isolation passed");
}

void c5vrx4_lane_print(void) {}
