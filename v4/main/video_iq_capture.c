/* C5VRX by Twotoz/contributors: bounded observational IQ export, not streaming. */
#include "video_internal.h"
#include <stdlib.h>

void video_export_iq_snapshot(void)
{
    if (s_menu_active || s_rssi_probe_active || s_pre_q4_probe_active ||
        phy_rx_lab_busy()) {
        printf("IQSNAP_REFUSED context_busy\n");
        return;
    }
    uint8_t *raw = malloc(C5V4_LEVEL_SAMPLE_BYTES);
    if (!raw) { printf("IQSNAP_REFUSED memory\n"); return; }
    rx_control_epoch_t epoch = {s_profile_generation, phy_rx_lab_generation(), s_gain_transition_count};
    rf_iq_lane_stats_t lanes;
    rf_get_iq_lane_stats(&lanes);
    uint64_t now = (uint64_t)esp_timer_get_time();
    bool settling = s_rx_profile == RX_PROFILE_DIRECT_GAIN &&
        s_agc_mode == ANALOG_AGC_ACTIVE && s_direct_gain_v3.state == DG3_SETTLE;
    unsigned gain = s_current_gain, lane = rf_get_iq_lanes(), freq = rf_get_frequency_mhz();
    bool source_ready = c5v4_level_source_ready(now, s_last_gain_write_us,
        s_last_phy_write_us, lanes.last_switch_us, settling);
    int64_t copy_start = esp_timer_get_time();
    bool copied = source_ready && video_copy_iq_snapshot(raw);
    int64_t copy_us = esp_timer_get_time() - copy_start;
    bool epoch_ok = epoch.profile == s_profile_generation && epoch.phy == phy_rx_lab_generation() &&
        epoch.gain == s_gain_transition_count && lane == rf_get_iq_lanes() && freq == rf_get_frequency_mhz();
    if (!source_ready || !copied || !epoch_ok) {
        free(raw);
        printf("IQSNAP_REFUSED stale_or_settling source_ready=%u copy_attempted=%u copied=%u "
               "epoch_ok=%u gain_settling=%u copy_us=%lld dma_ch=%d descriptors=%d\n",
               (unsigned)source_ready, (unsigned)source_ready, (unsigned)copied, (unsigned)epoch_ok,
               (unsigned)settling, (long long)copy_us, s_rx_dma_ch, s_rx_dscr_count);
        return;
    }
    uint32_t hash = 2166136261u;
    for (unsigned k = 0; k < C5V4_LEVEL_SAMPLE_BYTES; ++k) hash = (hash ^ raw[k]) * 16777619u;
    printf("IQSNAP_BEGIN bytes=%u rate_hz=%u format=Ihigh_Qlow gain=%u lane=%u freq_mhz=%u fnv1a=%08lx\n",
           C5V4_LEVEL_SAMPLE_BYTES, IQ_RATE_HZ, gain, lane, freq, (unsigned long)hash);
    static const char hex[] = "0123456789abcdef";
    char line[513];
    for (unsigned offset = 0; offset < C5V4_LEVEL_SAMPLE_BYTES; offset += 256) {
        unsigned size = C5V4_LEVEL_SAMPLE_BYTES-offset;
        if (size > 256) size = 256;
        for (unsigned k = 0; k < size; ++k) {
            line[2*k] = hex[raw[offset+k] >> 4];
            line[2*k+1] = hex[raw[offset+k] & 15];
        }
        line[2*size] = 0;
        printf("IQSNAP_DATA offset=%u %s\n", offset, line);
        vTaskDelay(1); /* Let IDLE/log consumers run; DMA owns live samples. */
    }
    free(raw);
    printf("IQSNAP_END fnv1a=%08lx\n", (unsigned long)hash);
}
