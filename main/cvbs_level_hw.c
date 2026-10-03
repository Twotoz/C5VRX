/* C5VRX by Twotoz and contributors. Adapted from C5VRX-4 PR #162.
 * ESP-IDF v6.0.2 ESP32-C5 LUT16 registers. Live arbitration unproven.
 * Never changes RUN, HALT, LUT width or phase-state bits during observation. */
#include "cvbs_level_hw.h"
#include "soc/bitscrambler_struct.h"
#include "hal/bitscrambler_ll.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_err.h"
#include <stdio.h>
#include <string.h>
static SemaphoreHandle_t lock;
static cvbs_level_t servo;
static uint16_t baseline[1024], applied[1024];
static bool ready;
static unsigned writes, faults;
void cvbs_level_hw_lock(void)
{
    /* First use is startup, before any observer/console tasks exist. */
    if (!lock) lock = xSemaphoreCreateRecursiveMutex();
    ESP_ERROR_CHECK(lock ? ESP_OK : ESP_ERR_NO_MEM);
    xSemaphoreTakeRecursive(lock, portMAX_DELAY);
}
void cvbs_level_hw_unlock(void) { xSemaphoreGiveRecursive(lock); }
static uint16_t read_entry(unsigned i)
{
    BITSCRAMBLER.lut_cfg[BITSCRAMBLER_DIR_TX].cfg0.lut_idx = i;
    return (uint16_t)BITSCRAMBLER.lut_cfg[BITSCRAMBLER_DIR_TX].cfg1.lut;
}
static void write_entry(unsigned i, uint16_t word)
{
    BITSCRAMBLER.lut_cfg[BITSCRAMBLER_DIR_TX].cfg0.lut_idx = i;
    BITSCRAMBLER.lut_cfg[BITSCRAMBLER_DIR_TX].cfg1.lut = word;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
}
void cvbs_level_hw_stop(void)
{
    cvbs_level_hw_lock(); ready = false; cvbs_level_hw_unlock();
}
bool cvbs_level_hw_prepare(void)
{
    /* Caller holds lock and engine is stopped, with fm.bsasm just loaded. */
    ready = false; cvbs_level_init(&servo);
    if (bitscrambler_ll_get_lut_width(&BITSCRAMBLER, BITSCRAMBLER_DIR_TX) != 1) return false;
    for (unsigned i = 0; i < 1024; ++i) baseline[i] = read_entry(i);
    unsigned probe = 32;
    uint16_t next = baseline[probe] ^ 1u;
    write_entry(probe, next);
    bool ok = true;
    for (unsigned i = 0; i < 1024; ++i)
        if (read_entry(i) != (i == probe ? next : baseline[i])) ok = false;
    write_entry(probe, baseline[probe]);
    for (unsigned i = 0; i < 1024; ++i)
        if (read_entry(i) != baseline[i]) ok = false;
    if (!ok) ++faults;
    memcpy(applied, baseline, sizeof(applied)); ready = ok;
    printf("C5V3_LEVEL startup_probe=%s live_arbitration=unproven\n", ok ? "pass" : "refused");
    return ok;
}
void cvbs_level_hw_analyze(uint8_t *raw, size_t n, cvbs_level_stats_t *out)
{
    cvbs_level_hw_lock();
    memset(out, 0, sizeof(*out));
    if (ready) cvbs_level_analyze(raw, n, baseline, out);
    cvbs_level_hw_unlock();
}
void cvbs_level_hw_observe(const cvbs_level_stats_t *v, bool fresh,
                           uint32_t context, uint64_t now)
{
    cvbs_level_hw_lock();
    if (!ready) goto done;
    if (bitscrambler_ll_get_lut_width(&BITSCRAMBLER, BITSCRAMBLER_DIR_TX) != 1) {
        ready = false; ++faults; goto done;
    }
    if (!cvbs_level_observe(&servo, v, fresh, context, now)) goto done;
    /* Golden has one shared 1024-entry LUT: ALL pair addresses, including
     * raw-decoder overlaps. Low six bits only; high phase bits stay exact. */
    for (unsigned i = 0; i < 1024; ++i) {
        uint16_t next = (baseline[i] & ~63u) | servo.codes[baseline[i] & 63u];
        if (next == applied[i]) continue;
        write_entry(i, next);
        if (read_entry(i) != next) { ready = false; ++faults; goto done; }
        applied[i] = next; ++writes;
    }
done:
    cvbs_level_hw_unlock();
}
void cvbs_level_hw_print(bool requested)
{
    cvbs_level_hw_lock();
    printf("C5V3_LEVEL requested=%u ready=%u experimental=1 updates=%u writes=%u faults=%u "
           "good=%u refused=%u sync_target_mv=0 blank_target_mv=300 "
           "dac_measured=0 loss=hold atomic_update=0 sync_regeneration=0\n",
           requested, ready, servo.updates, writes, faults, servo.good, servo.refusals);
    cvbs_level_hw_unlock();
}
