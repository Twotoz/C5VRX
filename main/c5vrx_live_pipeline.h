#pragma once

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "c5vrx_stream.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef esp_err_t (*c5vrx_cvbs_sink_fn)(const uint8_t *samples,
                                        size_t count,
                                        void *context);

typedef struct {
    c5vrx_rf_source_t *source;
    c5vrx_cvbs_sink_fn sink;
    void *sink_context;
    c5vrx_cvbs_conditioner_config_t conditioner;
    size_t maximum_input_words;
} c5vrx_live_pipeline_config_t;

esp_err_t c5vrx_live_pipeline_start(const c5vrx_live_pipeline_config_t *config);
esp_err_t c5vrx_live_pipeline_stop(void);
bool c5vrx_live_pipeline_running(void);
void c5vrx_live_pipeline_get_stats(c5vrx_stream_stats_t *stats);
void c5vrx_live_pipeline_log_stats(void);

/* Explicitly experimental adapter over repeated finite vendor dump triggers. */
esp_err_t c5vrx_finite_chain_source_create(c5vrx_rf_source_t *source,
                                           size_t words_per_block);
void c5vrx_finite_chain_source_destroy(c5vrx_rf_source_t *source);

#ifdef __cplusplus
}
#endif
