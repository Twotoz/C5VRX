/* C5VRX by Twotoz and contributors: completed circular IQ snapshot geometry. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define C5V4_LEVEL_SAMPLE_BYTES 8190u
typedef struct { size_t offset, first, safe_bytes; } c5v4_snapshot_plan_t;
static inline bool c5v4_snapshot_plan(size_t ring, size_t active_offset,
    size_t active_bytes, size_t bytes, c5v4_snapshot_plan_t *out)
{
    if (!out || !bytes || active_offset >= ring || !active_bytes ||
        active_bytes > ring-active_offset || bytes >= ring-active_bytes) return false;
    out->offset = (active_offset + ring - bytes) % ring;
    out->first = bytes < ring-out->offset ? bytes : ring-out->offset;
    out->safe_bytes = ring-bytes-active_bytes;
    return true;
}
static inline bool c5v4_snapshot_current(uint32_t before, uint32_t after,
    uint64_t elapsed_us, size_t safe_bytes, unsigned rate)
{
    return rate && before == after && elapsed_us <= 50 &&
        elapsed_us * rate < (uint64_t)safe_bytes * 1000000u;
}
