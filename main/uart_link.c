/**
 * uart_link.c - frames from the grabber to a display board over UART.
 * See uart_link.h for the packet format.
 */

#include "uart_link.h"

#include <string.h>

#include "driver/uart.h"
#include "sdkconfig.h"

#include "rf.h"

#if CONFIG_C5VRX_LINK_MODE

#define LINK_UART      UART_NUM_1
#define TX_BUFFER      49152        /* one 8-bit frame (38.6 KB) plus headroom */
#define RX_BUFFER      1024

static uint8_t s_crc_table[256];
static bool s_ready;

static void crc_init(void)
{
    for (unsigned i = 0u; i < 256u; ++i) {
        uint8_t c = (uint8_t)i;
        for (int k = 0; k < 8; ++k) c = (uint8_t)((c & 0x80u) ? (c << 1) ^ 0x07u : (c << 1));
        s_crc_table[i] = c;
    }
}

static uint8_t crc8(const uint8_t *p, size_t n)
{
    uint8_t c = 0u;
    while (n--) c = s_crc_table[c ^ *p++];
    return c;
}

esp_err_t uart_link_init(void)
{
    crc_init();
    const uart_config_t cfg = {
        .baud_rate = CONFIG_C5VRX_LINK_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(LINK_UART, RX_BUFFER, TX_BUFFER, 0, NULL, 0);
    if (err != ESP_OK) return err;
    if ((err = uart_param_config(LINK_UART, &cfg)) != ESP_OK) return err;
    err = uart_set_pin(LINK_UART, CONFIG_C5VRX_LINK_UART_TX_GPIO, CONFIG_C5VRX_LINK_UART_RX_GPIO,
                       UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (err != ESP_OK) return err;
    uint32_t baud = 0u;
    uart_get_baudrate(LINK_UART, &baud);
    s_ready = true;
    printf("[LINK] UART%d TX GPIO%d RX GPIO%d at %lu baud\n", (int)LINK_UART,
           CONFIG_C5VRX_LINK_UART_TX_GPIO, CONFIG_C5VRX_LINK_UART_RX_GPIO, (unsigned long)baud);
    return ESP_OK;
}

static void send_packet(uint8_t type, uint8_t frame, uint8_t row, const uint8_t *payload, size_t len)
{
    static uint8_t pkt[5u + GRAB_W + 1u];
    pkt[0] = UART_LINK_MAGIC0;
    pkt[1] = UART_LINK_MAGIC1;
    pkt[2] = type;
    pkt[3] = frame;
    pkt[4] = row;
    memcpy(pkt + 5, payload, len);
    pkt[5 + len] = crc8(pkt + 2, 3u + len);
    uart_write_bytes(LINK_UART, pkt, 6u + len);
}

static uint8_t error_code(const char *e)
{
    if (!e) return 0u;
    if (strstr(e, "horizontal")) return 1u;
    if (strstr(e, "vertical")) return 2u;
    if (strstr(e, "lost")) return 3u;
    if (strstr(e, "timeout")) return 4u;
    return 5u;
}

void uart_link_send_frame(const uint8_t *img, int rows, const grab_info_t *info,
                          uint8_t frame_id, bool pack4)
{
    if (!s_ready) return;
    uint8_t in[UART_LINK_INFO_LEN] = { 0 };
    uint16_t mhz = rf_get_frequency_mhz();
    uint16_t ms = (uint16_t)(info->grab_ms > 65535 ? 65535 : info->grab_ms);
    int ns = (int)(info->line_us * 1000.0f + 0.5f) - 60000;
    uint16_t line = (uint16_t)(ns < 0 ? 0 : ns > 65535 ? 65535 : ns);
    in[0] = 1u;
    in[1] = (uint8_t)(rows > 255 ? 255 : rows);
    in[2] = (uint8_t)((info->pal ? 1u : 0u) | (rows > 0 ? 2u : 0u) | (pack4 ? 4u : 0u));
    in[3] = (uint8_t)(mhz & 0xffu);
    in[4] = (uint8_t)(mhz >> 8);
    in[5] = (uint8_t)(rf_get_rx_gain_reg() >> 24);
    in[6] = error_code(info->error);
    in[7] = (uint8_t)(info->fields > 255 ? 255 : info->fields);
    in[8] = (uint8_t)(ms & 0xffu);
    in[9] = (uint8_t)(ms >> 8);
    in[10] = (uint8_t)(line & 0xffu);
    in[11] = (uint8_t)(line >> 8);
    send_packet(UART_LINK_T_INFO, frame_id, 0xffu, in, sizeof(in));
    if (rows <= 0) return;

    static uint8_t packed[GRAB_W / 2];
    for (int y = 0; y < GRAB_H; ++y) {
        const uint8_t *r = img + (size_t)y * GRAB_W;
        if (pack4) {
            for (int x = 0; x < GRAB_W / 2; ++x)
                packed[x] = (uint8_t)((r[2 * x] >> 4) | (r[2 * x + 1] & 0xf0u));
            send_packet(UART_LINK_T_ROW4, frame_id, (uint8_t)y, packed, sizeof(packed));
        } else {
            send_packet(UART_LINK_T_ROW8, frame_id, (uint8_t)y, r, GRAB_W);
        }
    }
}

#else

esp_err_t uart_link_init(void) { return ESP_ERR_NOT_SUPPORTED; }
void uart_link_send_frame(const uint8_t *img, int rows, const grab_info_t *info,
                          uint8_t frame_id, bool pack4)
{
    (void)img; (void)rows; (void)info; (void)frame_id; (void)pack4;
}

#endif
