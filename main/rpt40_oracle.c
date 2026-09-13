/* RF-off RX feasibility gate, not a live RPT40 receiver. */
#include "sdkconfig.h"
#if CONFIG_C5VRX2_MODE_RPT40_ORACLE
#include <stdlib.h>
#include <string.h>
#include "driver/bitscrambler.h"
#include "driver/parlio_rx.h"
#include "driver/parlio_tx.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "hal/parlio_ll.h"
#include "soc/parl_io_struct.h"
#include "startup_trace.h"

BITSCRAMBLER_PROGRAM(rx_program,"rpt40_rx");
#define N 8192u

static uint32_t hash(const uint8_t *data,size_t n)
{
    uint32_t h=2166136261u;
    while(n--) h=(h^*data++)*16777619u;
    return h;
}

static esp_err_t trial(unsigned index,uint8_t *raw,uint8_t *out)
{
    bool mapped=index&1;
    uint32_t rate=index<2?20000000:40000000;
    uint32_t h[32]={0x30545052,1,128,N,index,rate,mapped};
    h[7]=h[8]=h[9]=UINT32_MAX;
    h[14]=CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ*1000000u;
    parlio_tx_unit_handle_t tx=NULL;
    parlio_rx_unit_handle_t rx=NULL;
    parlio_rx_delimiter_handle_t delim=NULL;
    bitscrambler_handle_t bs=NULL;
    bool te=false,re=false,be=false;
    memset(out,0xa5,N);
    c5vrx2_trace_stage(0x900+index,ESP_OK);
    const parlio_tx_unit_config_t tc={
        .clk_src=PARLIO_CLK_SRC_DEFAULT,.clk_in_gpio_num=-1,
        .output_clk_freq_hz=rate,.data_width=8,
        .data_gpio_nums={1,0,25,7,10,5,3,4},
        .clk_out_gpio_num=-1,.valid_gpio_num=-1,.trans_queue_depth=1,
        .max_transfer_size=N,.dma_burst_size=32,
        .shift_edge=PARLIO_SHIFT_EDGE_NEG,.bit_pack_order=PARLIO_BIT_PACK_ORDER_LSB,
    };
    esp_err_t err=parlio_new_tx_unit(&tc,&tx);
    if(err!=ESP_OK) goto done;
    const parlio_rx_unit_config_t rc={
        .trans_queue_depth=1,.max_recv_size=N,.dma_burst_size=32,
        .data_width=8,.clk_src=PARLIO_CLK_SRC_DEFAULT,.exp_clk_freq_hz=rate,
        .clk_in_gpio_num=-1,.clk_out_gpio_num=-1,.valid_gpio_num=-1,
        .data_gpio_nums={1,0,25,7,10,5,3,4},.flags.free_clk=true,
    };
    err=parlio_new_rx_unit(&rc,&rx);
    if(err!=ESP_OK) goto done;
    const parlio_rx_soft_delimiter_config_t dc={.sample_edge=PARLIO_SAMPLE_EDGE_POS,
        .bit_pack_order=PARLIO_BIT_PACK_ORDER_LSB,.eof_data_len=N};
    err=parlio_new_rx_soft_delimiter(&dc,&delim);
    if(err!=ESP_OK) goto done;
    if(mapped){
        const bitscrambler_config_t bc={.dir=BITSCRAMBLER_DIR_RX,
            .attach_to=SOC_BITSCRAMBLER_ATTACH_PARL_IO};
        err=bitscrambler_new(&bc,&bs);
        if(err!=ESP_OK) goto done;
        err=bitscrambler_enable(bs);
        if(err!=ESP_OK) goto done;
        be=true;
        err=bitscrambler_load_program(bs,rx_program);
        if(err!=ESP_OK) goto done;
        err=bitscrambler_reset(bs);
        if(err!=ESP_OK) goto done;
        err=bitscrambler_start(bs);
        if(err!=ESP_OK) goto done;
    }
    err=parlio_rx_unit_enable(rx,true);
    if(err!=ESP_OK) goto done;
    re=true;
    const parlio_receive_config_t receive={.delimiter=delim};
    err=parlio_rx_unit_receive(rx,out,N,&receive);
    if(err!=ESP_OK) goto done;
    err=parlio_tx_unit_enable(tx);
    if(err!=ESP_OK) goto done;
    te=true;
    parlio_ll_enable_interrupt(&PARL_IO,PARLIO_LL_EVENT_TX_FIFO_EMPTY,false);
    const parlio_transmit_config_t transmit={.flags.loop_transmission=true};
    err=parlio_tx_unit_transmit(tx,raw,N*8,&transmit);
    h[7]=err;
    if(err!=ESP_OK) goto done;
    esp_rom_delay_us(2000);
    h[10]=PARL_IO.int_raw.val;
    int64_t start=esp_timer_get_time();
    err=parlio_rx_soft_delimiter_start_stop(rx,delim,true);
    if(err==ESP_OK) err=parlio_rx_unit_wait_all_done(rx,1000);
    h[8]=err;h[11]=PARL_IO.int_raw.val;h[12]=esp_timer_get_time()-start;
done:
    if(re){(void)parlio_rx_soft_delimiter_start_stop(rx,delim,false);(void)parlio_rx_unit_disable(rx);}
    if(te)(void)parlio_tx_unit_disable(tx);
    if(be)(void)bitscrambler_disable(bs);
    if(bs)bitscrambler_free(bs);
    if(delim)(void)parlio_del_rx_delimiter(delim);
    if(rx)(void)parlio_del_rx_unit(rx);
    if(tx)(void)parlio_del_tx_unit(tx);
    h[9]=err;h[13]=hash(raw,N);h[15]=hash(out,N);
    const esp_partition_t *p=esp_partition_find_first(ESP_PARTITION_TYPE_DATA,0x42,"diagcap");
    size_t offset=index*0x5000;
    esp_err_t saved=p?ESP_OK:ESP_ERR_NOT_FOUND;
    if(p && offset+0x5000>p->size)saved=ESP_ERR_INVALID_SIZE;
    if(saved==ESP_OK)saved=esp_partition_erase_range(p,offset,0x5000);
    if(saved==ESP_OK)saved=esp_partition_write(p,offset+128,raw,N);
    if(saved==ESP_OK)saved=esp_partition_write(p,offset+128+N,out,N);
    if(saved==ESP_OK)saved=esp_partition_write(p,offset,h,sizeof(h));
    c5vrx2_trace_stage_detail(0x910+index,saved,h[9],h[12],h[11]);
    return saved!=ESP_OK?saved:err;
}

esp_err_t c5vrx2_rpt40_oracle_run(void)
{
    uint8_t *raw=heap_caps_malloc(N,MALLOC_CAP_DMA|MALLOC_CAP_INTERNAL);
    uint8_t *out=heap_caps_malloc(N,MALLOC_CAP_DMA|MALLOC_CAP_INTERNAL);
    if(!raw || !out){free(raw);free(out);return ESP_ERR_NO_MEM;}
    uint32_t state=0x173940ab;
    for(unsigned i=0;i<N;++i){state^=state<<13;state^=state>>17;state^=state<<5;raw[i]=state;}
    esp_err_t result=ESP_OK;
    for(unsigned i=0;i<4;++i){esp_err_t err=trial(i,raw,out);if(err!=ESP_OK)result=err;}
    free(raw);free(out);
    c5vrx2_trace_stage(0x91f,result);
    return result; /* transport completion only: host full-byte gate required */
}
#endif
