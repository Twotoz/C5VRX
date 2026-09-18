/**
 * main.cpp - LilyGo T-Embed CC1101: display for the C5VRX UART video link.
 *
 * The XIAO ESP32-C5 running C5VRX link mode grabs 224x168 luma frames from
 * the analog FPV signal and streams them over UART at 4 Mbaud (packet
 * format: main/uart_link.h of the C5 firmware). This board receives them on
 * its UART connector (GPIO44 = RXD), converts each row to RGB565 and shows
 * the frame centred on the 320x170 ST7789, with the link status in the left
 * margin.
 *
 * Wiring: XIAO D6 (GPIO11, TX) -> T-Embed RXD (GPIO44)
 *         XIAO D7 (GPIO12, RX) <- T-Embed TXD (GPIO43)   (commands, later)
 *         GND - GND
 *
 * Encoder button: rotate the picture by 180 degrees.
 */

#include <Arduino.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "driver/uart.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "freertos/semphr.h"

#include "font3x5.h"

/* ---------------------------------------------------------------- board */
#define PIN_PWR_EN    15    /* board power latch: must be driven high early */
#define PIN_LCD_BL    21
#define PIN_LCD_RST   40    /* display reset (as in the working 1101view project) */
#define PIN_LCD_CS    41
#define PIN_LCD_DC    16
#define PIN_SPI_SCLK  11
#define PIN_SPI_MOSI  9
#define PIN_SPI_MISO  10
#define PIN_CC1101_CS 12    /* shares the SPI bus: keep deselected */
#define PIN_SD_CS     13
#define PIN_ENC_KEY   0
#define PIN_LINK_RX   44    /* UART connector RXD */
#define PIN_LINK_TX   43    /* UART connector TXD */

#define LCD_W 320
#define LCD_H 170
#define LCD_SPI_HZ (40 * 1000 * 1000)

/* ---------------------------------------------------------------- link */
#define LINK_UART   UART_NUM_1
#define LINK_BAUD   4000000
#define IMG_W       224
#define IMG_H       168
#define IMG_X       ((LCD_W - IMG_W) / 2 + 20)   /* leave a 68-pixel status margin on the left */
#define IMG_Y       1
#define MAGIC0      0xA5
#define MAGIC1      0x5A
#define T_INFO      0x10
#define T_ROW8      0x01
#define T_ROW4      0x02
#define INFO_LEN    16

static esp_lcd_panel_handle_t s_panel;
static SemaphoreHandle_t s_lcd_done;
static uint16_t *s_fb;              /* IMG_W x IMG_H, RGB565 byte-swapped for SPI */
static uint16_t s_gray[256];
static uint8_t s_crc_table[256];
static bool s_flipped;

/* Link statistics. */
static uint32_t s_frames_drawn, s_rows, s_crc_errors, s_bytes;
static uint8_t s_info[INFO_LEN];
static bool s_have_info;
static uint32_t s_last_frame_ms, s_last_byte_ms;

/* ---------------------------------------------------------------- display */

static bool IRAM_ATTR lcd_done_cb(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *edata, void *ctx)
{
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(s_lcd_done, &woken);
    return woken == pdTRUE;
}

static void lcd_draw(int x0, int y0, int w, int h, const uint16_t *pixels)
{
    esp_lcd_panel_draw_bitmap(s_panel, x0, y0, x0 + w, y0 + h, pixels);
    xSemaphoreTake(s_lcd_done, pdMS_TO_TICKS(100));   /* buffer must stay valid until sent */
}

static void lcd_orientation(void)
{
    /* Landscape as TFT_eSPI rotation 3 (MADCTL MV | MY), the orientation the
     * 1101view project uses on this board; flipped = rotation 1. */
    esp_lcd_panel_swap_xy(s_panel, true);
    esp_lcd_panel_mirror(s_panel, s_flipped, !s_flipped);
}

static void lcd_init(void)
{
    spi_bus_config_t bus = {};
    bus.mosi_io_num = PIN_SPI_MOSI;
    bus.miso_io_num = PIN_SPI_MISO;
    bus.sclk_io_num = PIN_SPI_SCLK;
    bus.quadwp_io_num = -1;
    bus.quadhd_io_num = -1;
    bus.max_transfer_sz = IMG_W * IMG_H * 2 + 64;
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));

    esp_lcd_panel_io_handle_t io = NULL;
    esp_lcd_panel_io_spi_config_t io_cfg = {};
    io_cfg.cs_gpio_num = PIN_LCD_CS;
    io_cfg.dc_gpio_num = PIN_LCD_DC;
    io_cfg.spi_mode = 0;
    io_cfg.pclk_hz = LCD_SPI_HZ;
    io_cfg.trans_queue_depth = 4;
    io_cfg.on_color_trans_done = lcd_done_cb;
    io_cfg.lcd_cmd_bits = 8;
    io_cfg.lcd_param_bits = 8;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_cfg, &io));

    esp_lcd_panel_dev_config_t dev = {};
    dev.reset_gpio_num = PIN_LCD_RST;
    dev.color_space = ESP_LCD_COLOR_SPACE_RGB;
    dev.bits_per_pixel = 16;
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &dev, &s_panel));
    esp_lcd_panel_reset(s_panel);
    esp_lcd_panel_init(s_panel);
    esp_lcd_panel_invert_color(s_panel, true);
    esp_lcd_panel_set_gap(s_panel, 0, 35);     /* 170-line panel in the 240-line RAM */
    lcd_orientation();
    esp_lcd_panel_disp_on_off(s_panel, true);
}

static inline uint16_t rgb565_swapped(uint8_t r, uint8_t g, uint8_t b)
{
    uint16_t c = (uint16_t)(((r & 0xf8) << 8) | ((g & 0xfc) << 3) | (b >> 3));
    return (uint16_t)((c >> 8) | (c << 8));
}

static void lcd_fill(int x0, int y0, int w, int h, uint16_t color)
{
    static uint16_t line[LCD_W];
    for (int i = 0; i < w; ++i) line[i] = color;
    for (int y = y0; y < y0 + h; ++y) lcd_draw(x0, y, w, 1, line);
}

/* Text: 3x5 glyphs, scale s, one line of up to `maxc` characters. */
static void lcd_text(int x0, int y0, const char *text, int s, uint16_t fg, uint16_t bg, int maxc)
{
    static uint16_t buf[LCD_W * 5 * 4];
    int cw = 4 * s;                        /* glyph 3 + 1 spacing */
    int w = maxc * cw, h = 5 * s;
    if (w > LCD_W) w = LCD_W;
    for (int i = 0; i < w * h; ++i) buf[i] = bg;
    for (int c = 0; c < maxc && text[c]; ++c) {
        uint16_t g = font3x5_glyph(text[c]);
        for (int row = 0; row < 5; ++row)
            for (int col = 0; col < 3; ++col)
                if (g & (1u << (14 - row * 3 - col)))
                    for (int dy = 0; dy < s; ++dy)
                        for (int dx = 0; dx < s; ++dx) {
                            int x = c * cw + col * s + dx, y = row * s + dy;
                            if (x < w) buf[y * w + x] = fg;
                        }
    }
    lcd_draw(x0, y0, w, h, buf);
}

/* ---------------------------------------------------------------- link */

static void crc_init(void)
{
    for (unsigned i = 0; i < 256; ++i) {
        uint8_t c = (uint8_t)i;
        for (int k = 0; k < 8; ++k) c = (uint8_t)((c & 0x80) ? (c << 1) ^ 0x07 : (c << 1));
        s_crc_table[i] = c;
    }
}

static void link_init(void)
{
    uart_config_t cfg = {};
    cfg.baud_rate = LINK_BAUD;
    cfg.data_bits = UART_DATA_8_BITS;
    cfg.parity = UART_PARITY_DISABLE;
    cfg.stop_bits = UART_STOP_BITS_1;
    cfg.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    cfg.source_clk = UART_SCLK_APB;
    ESP_ERROR_CHECK(uart_driver_install(LINK_UART, 32768, 0, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(LINK_UART, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(LINK_UART, PIN_LINK_TX, PIN_LINK_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    uart_set_rx_timeout(LINK_UART, 2);
}

static void draw_frame(void)
{
    lcd_draw(IMG_X, IMG_Y, IMG_W, IMG_H, s_fb);
    s_frames_drawn++;
    s_last_frame_ms = millis();
}

static void handle_packet(uint8_t type, uint8_t frame, uint8_t row, const uint8_t *p)
{
    static int last_frame = -1;
    static int rows_this_frame;
    if (type == T_INFO) {
        if (last_frame >= 0 && frame != last_frame && rows_this_frame > 0) draw_frame();   /* last rows lost */
        memcpy(s_info, p, INFO_LEN);
        s_have_info = true;
        last_frame = frame;
        rows_this_frame = 0;
        return;
    }
    if (row >= IMG_H) return;
    uint16_t *dst = s_fb + (size_t)row * IMG_W;
    if (type == T_ROW8) {
        for (int x = 0; x < IMG_W; ++x) dst[x] = s_gray[p[x]];
    } else {
        for (int x = 0; x < IMG_W / 2; ++x) {
            dst[2 * x] = s_gray[(uint8_t)((p[x] & 0x0f) * 17)];
            dst[2 * x + 1] = s_gray[(uint8_t)((p[x] >> 4) * 17)];
        }
    }
    s_rows++;
    rows_this_frame++;
    if (row == IMG_H - 1) {
        draw_frame();
        rows_this_frame = 0;
    }
}

/* Byte-wise packet parser: A5 5A type frame row payload crc. */
static void parse(const uint8_t *data, int n)
{
    static uint8_t pkt[5 + IMG_W + 1];
    static int pos, need;
    for (int i = 0; i < n; ++i) {
        uint8_t b = data[i];
        if (pos == 0) { if (b == MAGIC0) pkt[pos++] = b; continue; }
        if (pos == 1) { if (b == MAGIC1) pkt[pos++] = b; else pos = (b == MAGIC0) ? 1 : 0; continue; }
        pkt[pos++] = b;
        if (pos == 3) {
            need = (b == T_INFO) ? INFO_LEN : (b == T_ROW8) ? IMG_W : (b == T_ROW4) ? IMG_W / 2 : -1;
            if (need < 0) { pos = 0; continue; }
            need += 6;
        }
        if (pos >= 3 && pos == need) {
            uint8_t c = 0;
            for (int k = 2; k < need - 1; ++k) c = s_crc_table[c ^ pkt[k]];
            if (c == pkt[need - 1]) handle_packet(pkt[2], pkt[3], pkt[4], pkt + 5);
            else s_crc_errors++;
            pos = 0;
        }
    }
}

/* ---------------------------------------------------------------- status */

static const char *error_text(uint8_t e)
{
    switch (e) {
    case 0: return "OK";
    case 1: return "NO HSYNC";
    case 2: return "NO VSYNC";
    case 3: return "LOST";
    case 4: return "TIMEOUT";
    default: return "ERROR";
    }
}

static void draw_status(float fps)
{
    const uint16_t fg = rgb565_swapped(255, 255, 255), dim = rgb565_swapped(140, 140, 140);
    const uint16_t bg = rgb565_swapped(0, 0, 0);
    const uint16_t ok = rgb565_swapped(40, 220, 60), bad = rgb565_swapped(230, 50, 40);
    char t[16];
    bool linked = millis() - s_last_byte_ms < 500;
    bool video = s_have_info && s_info[1] > 0 && millis() - s_last_frame_ms < 500;
    lcd_text(2, 4, linked ? (video ? "VIDEO" : "NO SIG") : "NO LINK", 2, video ? ok : bad, bg, 8);
    if (s_have_info) {
        unsigned mhz = s_info[3] | (s_info[4] << 8);
        snprintf(t, sizeof(t), "%u", mhz);
        lcd_text(2, 20, t, 2, fg, bg, 8);
        lcd_text(2, 32, "MHZ", 2, dim, bg, 8);
        lcd_text(2, 48, (s_info[2] & 1) ? "PAL" : "NTSC", 2, fg, bg, 8);
        snprintf(t, sizeof(t), "G%u", s_info[5]);
        lcd_text(2, 64, t, 2, fg, bg, 8);
        lcd_text(2, 80, error_text(s_info[6]), 2, s_info[6] ? bad : dim, bg, 8);
    }
    snprintf(t, sizeof(t), "%.1f", fps);
    lcd_text(2, 110, t, 2, fg, bg, 8);
    lcd_text(2, 122, "FPS", 2, dim, bg, 8);
    snprintf(t, sizeof(t), "E%lu", (unsigned long)(s_crc_errors > 999 ? 999 : s_crc_errors));
    lcd_text(2, 146, t, 2, s_crc_errors ? bad : dim, bg, 8);
}

/* ---------------------------------------------------------------- main */

void setup(void)
{
    Serial.begin(115200);
    pinMode(PIN_PWR_EN, OUTPUT);
    digitalWrite(PIN_PWR_EN, HIGH);
    pinMode(PIN_CC1101_CS, OUTPUT);
    digitalWrite(PIN_CC1101_CS, HIGH);
    pinMode(PIN_SD_CS, OUTPUT);
    digitalWrite(PIN_SD_CS, HIGH);
    pinMode(PIN_ENC_KEY, INPUT_PULLUP);
    delay(50);

    s_lcd_done = xSemaphoreCreateBinary();
    s_fb = (uint16_t *)heap_caps_malloc(IMG_W * IMG_H * 2, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    for (int i = 0; i < 256; ++i) s_gray[i] = rgb565_swapped((uint8_t)i, (uint8_t)i, (uint8_t)i);
    for (int i = 0; i < IMG_W * IMG_H; ++i) s_fb[i] = s_gray[(i / IMG_W) * 255 / IMG_H];   /* test ramp */
    crc_init();

    lcd_init();
    lcd_fill(0, 0, LCD_W, LCD_H, rgb565_swapped(0, 0, 0));
    pinMode(PIN_LCD_BL, OUTPUT);
    digitalWrite(PIN_LCD_BL, HIGH);
    draw_frame();                                  /* grey ramp: display works */
    lcd_text(IMG_X + 36, IMG_Y + 76, "C5VRX LINK", 4, rgb565_swapped(255, 255, 255), rgb565_swapped(0, 0, 0), 10);
    s_frames_drawn = 0;

    link_init();
    Serial.printf("C5VRX T-Embed link: UART%d RX GPIO%d at %d baud, image %dx%d at %d,%d\n",
                  (int)LINK_UART, PIN_LINK_RX, LINK_BAUD, IMG_W, IMG_H, IMG_X, IMG_Y);
}

void loop(void)
{
    static uint8_t buf[4096];
    static uint32_t t_status, frames_at_status, bytes_at_status;
    static bool key_was_down;

    int n = uart_read_bytes(LINK_UART, buf, sizeof(buf), pdMS_TO_TICKS(10));
    if (n > 0) {
        s_bytes += (uint32_t)n;
        s_last_byte_ms = millis();
        parse(buf, n);
    }

    bool key_down = digitalRead(PIN_ENC_KEY) == LOW;
    if (key_down && !key_was_down) {
        s_flipped = !s_flipped;
        lcd_orientation();
        lcd_fill(0, 0, LCD_W, LCD_H, rgb565_swapped(0, 0, 0));
    }
    key_was_down = key_down;

    uint32_t now = millis();
    if (now - t_status >= 1000) {
        float dt = (now - t_status) / 1000.0f;
        float fps = (s_frames_drawn - frames_at_status) / dt;
        float kbps = (s_bytes - bytes_at_status) / dt / 1024.0f;
        draw_status(fps);
        Serial.printf("fps %.1f  link %.0f KB/s  rows %lu  crc errors %lu  info rows %u err %u freq %u\n",
                      fps, kbps, (unsigned long)s_rows, (unsigned long)s_crc_errors,
                      s_have_info ? s_info[1] : 0, s_have_info ? s_info[6] : 0,
                      s_have_info ? (unsigned)(s_info[3] | (s_info[4] << 8)) : 0);
        t_status = now;
        frames_at_status = s_frames_drawn;
        bytes_at_status = s_bytes;
    }
}
