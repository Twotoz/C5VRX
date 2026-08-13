#include "c5vrx_rf_dump_producer.h"

#include "sdkconfig.h"
#include "esp_idf_version.h"

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))

#define DUMP_CTRL       0x600a9004u
#define DUMP_PTR_MODE   0x600a9008u
#define DUMP_FORMAT     0x600a9018u
#define FE_PATH         0x600a20b4u
#define FE_AUX          0x600a20acu
#define FE_ENABLE       0x600a0800u
#define SOURCE_CTRL     0x600a08ccu
#define SOURCE_MUX      0x600a70b8u
#define RX_CLOCK        0x60095004u
#define PHY_CLEAR       0x600a9c04u

#define CTRL_LENGTH_MASK 0x0001ffffu
#define CTRL_MODE_BIT    0x00020000u
#define CTRL_STATUS_BIT  0x00040000u
#define CTRL_ENABLE_BIT  0x80000000u
#define MODE_SELECT_MASK 0x01fe0000u

extern void phy_pbus_clear_reg(void);

static bool s_configured;
static bool s_running;
static struct {
    uint32_t dump_ctrl, dump_ptr_mode, dump_format;
    uint32_t fe_path, fe_aux, fe_enable, source_ctrl, source_mux;
    uint32_t rx_clock, phy_clear;
} s_saved;

bool c5vrx_rf_dump_producer_available(void)
{
#if CONFIG_C5VRX_EXPERIMENTAL_RF_DUMP_PRODUCER && \
    ESP_IDF_VERSION == ESP_IDF_VERSION_VAL(6, 0, 2)
    return true;
#else
    return false;
#endif
}

static void set_format_fields(uint32_t top, uint32_t middle,
                              uint32_t low, uint32_t bottom)
{
    uint32_t value = REG32(DUMP_FORMAT);
    value = (value & 0xff03ffffu) | top;
    REG32(DUMP_FORMAT) = value;
    value = REG32(DUMP_FORMAT);
    value = (value & 0xfffc0fffu) | middle;
    REG32(DUMP_FORMAT) = value;
    value = REG32(DUMP_FORMAT);
    value = (value & 0xfffff03fu) | low;
    REG32(DUMP_FORMAT) = value;
    value = REG32(DUMP_FORMAT);
    value = (value & 0xffffffc0u) | bottom;
    REG32(DUMP_FORMAT) = value;
}

esp_err_t c5vrx_rf_dump_configure(size_t sample_count,
                                  c5vrx_rf_dump_mode_t mode)
{
    if (!c5vrx_rf_dump_producer_available()) return ESP_ERR_NOT_SUPPORTED;
    if (s_running) return ESP_ERR_INVALID_STATE;
    if (sample_count == 0 || sample_count > 16384u) return ESP_ERR_INVALID_ARG;
    if (mode != C5VRX_RF_DUMP_MODE_ORDINARY_RX &&
        mode != C5VRX_RF_DUMP_MODE_11 && mode != C5VRX_RF_DUMP_MODE_12)
        return ESP_ERR_NOT_SUPPORTED;

    /* Snapshot only values actually touched below. Refuse to take ownership of
     * an already-running engine, then restore this snapshot after the audited
     * vendor teardown. These are observed values, not guessed reset values. */
    s_saved.dump_ctrl = REG32(DUMP_CTRL);
    if (s_saved.dump_ctrl & CTRL_ENABLE_BIT) return ESP_ERR_INVALID_STATE;
    s_saved.dump_ptr_mode = REG32(DUMP_PTR_MODE);
    s_saved.dump_format = REG32(DUMP_FORMAT);
    s_saved.fe_path = REG32(FE_PATH);
    s_saved.fe_aux = REG32(FE_AUX);
    s_saved.fe_enable = REG32(FE_ENABLE);
    s_saved.source_ctrl = REG32(SOURCE_CTRL);
    s_saved.source_mux = REG32(SOURCE_MUX);
    s_saved.rx_clock = REG32(RX_CLOCK);
    s_saved.phy_clear = REG32(PHY_CLEAR);

    /* Exact set_dump_mode(0): all three supported trigger modes use the
     * ordinary receive source; nonzero set_dump_mode arguments are identical. */
    REG32(SOURCE_CTRL) &= 0xff87ffffu;
    REG32(SOURCE_MUX) = (REG32(SOURCE_MUX) & 0xfffffff8u) | 1u;

    /* Exact automatic-gain subset of C5 v6.0.2 adctrig. The historical
     * sample_80m write is intentionally absent: vendor control flow overwrites
     * those same bits before enable, so forcing them would be a new value. */
    REG32(PHY_CLEAR) = UINT32_MAX;
    REG32(FE_ENABLE) |= 4u;
    REG32(DUMP_CTRL) = (REG32(DUMP_CTRL) & ~CTRL_LENGTH_MASK) |
                       ((uint32_t)sample_count & CTRL_LENGTH_MASK);
    REG32(DUMP_CTRL) &= ~CTRL_MODE_BIT;

    if (mode == C5VRX_RF_DUMP_MODE_ORDINARY_RX) {
        set_format_fields(0x006c0000u, 0x0001a000u, 0x00000640u, 0x18u);
    } else if (mode == C5VRX_RF_DUMP_MODE_11) {
        set_format_fields(0x005c0000u, 0x00016000u, 0x00000540u, 0x14u);
    } else {
        set_format_fields(0x002c0000u, 0x00008000u, 0x00000540u, 0x14u);
    }

    REG32(DUMP_FORMAT) |= 0x01000000u;
    REG32(RX_CLOCK) = (REG32(RX_CLOCK) & 0xfffff0ffu) | 0x00000200u;
    REG32(RX_CLOCK) |= 0x00010000u;
    REG32(DUMP_CTRL) &= ~0x00300000u;
    REG32(DUMP_CTRL) &= ~CTRL_MODE_BIT;

    REG32(FE_PATH) &= ~1u;
    if (mode == C5VRX_RF_DUMP_MODE_ORDINARY_RX) {
        /* Exact mode-0 branch: unlike modes 1..12 it ORs, rather than clears,
         * the overlapping selector. This is why arbitrary rate forcing is not
         * part of this reconstruction. */
        REG32(DUMP_PTR_MODE) |= 0x01e00000u;
    } else {
        REG32(DUMP_PTR_MODE) =
            (REG32(DUMP_PTR_MODE) & ~MODE_SELECT_MASK) |
            (mode == C5VRX_RF_DUMP_MODE_11 ? 0x00160000u : 0x00120000u);
        if (mode == C5VRX_RF_DUMP_MODE_12) {
            REG32(PHY_CLEAR) &= ~0x00200000u;
            REG32(FE_PATH) |= 1u;
            REG32(FE_AUX) = (REG32(FE_AUX) & 0x1fffffffu) | 0x40000000u;
        }
    }

    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
    s_configured = true;
    return ESP_OK;
}

esp_err_t c5vrx_rf_dump_start(void)
{
    if (!c5vrx_rf_dump_producer_available()) return ESP_ERR_NOT_SUPPORTED;
    if (!s_configured || s_running) return ESP_ERR_INVALID_STATE;
    REG32(DUMP_CTRL) |= CTRL_ENABLE_BIT;
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
    s_running = true;
    return ESP_OK;
}

esp_err_t c5vrx_rf_dump_get_status(c5vrx_rf_dump_status_t *status)
{
    if (!status) return ESP_ERR_INVALID_ARG;
    if (!c5vrx_rf_dump_producer_available()) return ESP_ERR_NOT_SUPPORTED;
    const uint32_t control = REG32(DUMP_CTRL);
    const uint32_t pointer_mode = REG32(DUMP_PTR_MODE);
    *status = (c5vrx_rf_dump_status_t) {
        .control = control, .pointer_mode = pointer_mode,
        .format = REG32(DUMP_FORMAT), .source_mux = REG32(SOURCE_MUX),
        .fe_path = REG32(FE_PATH), .fe_aux = REG32(FE_AUX),
        .pointer = (uint16_t)pointer_mode,
        .enabled = (control & CTRL_ENABLE_BIT) != 0,
        .wrap_or_done = (control & CTRL_STATUS_BIT) != 0,
    };
    return ESP_OK;
}

esp_err_t c5vrx_rf_dump_stop(void)
{
    if (!c5vrx_rf_dump_producer_available()) return ESP_ERR_NOT_SUPPORTED;
    if (!s_configured) return ESP_ERR_INVALID_STATE;
    REG32(DUMP_CTRL) &= ~CTRL_ENABLE_BIT;
    REG32(RX_CLOCK) &= 0xfffff0ffu;
    REG32(RX_CLOCK) &= 0xfffeffffu;
    phy_pbus_clear_reg();
    REG32(DUMP_PTR_MODE) = s_saved.dump_ptr_mode;
    REG32(DUMP_FORMAT) = s_saved.dump_format;
    REG32(FE_PATH) = s_saved.fe_path;
    REG32(FE_AUX) = s_saved.fe_aux;
    REG32(FE_ENABLE) = s_saved.fe_enable;
    REG32(SOURCE_CTRL) = s_saved.source_ctrl;
    REG32(SOURCE_MUX) = s_saved.source_mux;
    REG32(RX_CLOCK) = s_saved.rx_clock;
    REG32(PHY_CLEAR) = s_saved.phy_clear;
    REG32(DUMP_CTRL) = s_saved.dump_ctrl & ~CTRL_ENABLE_BIT;
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");
    s_running = false;
    s_configured = false;
    return ESP_OK;
}
