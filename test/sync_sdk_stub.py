"""C5VRX by Twotoz/contributors: shared ESP-IDF register/timer stubs for the
sync flywheel host runtime tests."""

SDK = r'''
#ifndef MOCK_SDK_H
#define MOCK_SDK_H
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define IRAM_ATTR
#define ESP_OK 0
#define GPTIMER_CLK_SRC_DEFAULT 0
#define GPTIMER_COUNT_UP 0
typedef int esp_err_t;
typedef struct mock_timer *gptimer_handle_t;
typedef struct { uint64_t count_value,alarm_value; } gptimer_alarm_event_data_t;
typedef struct { uint64_t alarm_count; } gptimer_alarm_config_t;
typedef struct { int clk_src,direction;uint32_t resolution_hz;int intr_priority; } gptimer_config_t;
typedef struct { bool (*on_alarm)(gptimer_handle_t,const gptimer_alarm_event_data_t*,void*); } gptimer_event_callbacks_t;
int gptimer_new_timer(const gptimer_config_t*,gptimer_handle_t*);
int gptimer_register_event_callbacks(gptimer_handle_t,const gptimer_event_callbacks_t*,void*);
int gptimer_enable(gptimer_handle_t);
int gptimer_disable(gptimer_handle_t);
int gptimer_start(gptimer_handle_t);
int gptimer_stop(gptimer_handle_t);
int gptimer_del_timer(gptimer_handle_t);
int gptimer_set_raw_count(gptimer_handle_t,uint64_t);
int gptimer_get_raw_count(gptimer_handle_t,uint64_t*);
int gptimer_set_alarm_action(gptimer_handle_t,const gptimer_alarm_config_t*);
int esp_clk_cpu_freq(void);
uint32_t esp_cpu_get_cycle_count(void);
int64_t esp_timer_get_time(void);
void vTaskDelay(unsigned);
uint32_t mock_read(uint32_t);
void mock_write(uint32_t,uint32_t);
#define REG_READ(r) mock_read(r)
#define REG_WRITE(r,v) mock_write(r,v)
#define GPIO_ENABLE_REG 0u
#define GPIO_ENABLE_W1TS_REG 4u
#define GPIO_ENABLE_W1TC_REG 8u
#define GPIO_FUNC0_OUT_SEL_CFG_REG 0x100u
#define GPIO_FUNC0_OUT_SEL_M 511u
#define GPIO_FUNC0_OUT_INV_SEL (1u<<9)
#define GPIO_FUNC0_OE_SEL (1u<<10)
#define GPIO_FUNC0_OE_INV_SEL (1u<<11)
#define PARL_TX_DATA0_IDX 48u
#define PARL_TX_DATA1_IDX 49u
#define PARL_TX_DATA2_IDX 50u
#define PARL_TX_DATA3_IDX 51u
#define PARL_TX_DATA4_IDX 52u
#define PARL_TX_DATA5_IDX 53u
#define FUN_PU (1u<<8)
#define FUN_PD (1u<<7)
#define IO_MUX_GPIO23_REG (0x300u+4u*23u)
#define IO_MUX_GPIO24_REG (0x300u+4u*24u)
#define IO_MUX_GPIO11_REG (0x300u+4u*11u)
#define IO_MUX_GPIO12_REG (0x300u+4u*12u)
#define IO_MUX_GPIO8_REG (0x300u+4u*8u)
#define IO_MUX_GPIO9_REG (0x300u+4u*9u)
#endif
'''
