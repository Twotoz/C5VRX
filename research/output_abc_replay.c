/* C5VRX by Twotoz/contributors: offline same-IQ OUTPUT comparison.
 * Ring replay adapted from test/test_sync_flywheel.c. Actual root worker
 * tables, output_entry and real-source-only keeper; never accesses hardware. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sync_flywheel.h"
#include "phase8_gain_lut.h"
#include "kp_tables.h"
#include "output_level.h"

#define RING 32768u
#define DSCR 4092u
#define LAG 16384u
typedef struct { uint8_t *ring; } executor_t;
static bool commit(void *opaque, uint64_t start, const uint8_t *src, uint32_t bytes)
{
    executor_t *e = opaque;
    for (uint32_t k = 0; k < bytes; ++k) e->ring[(start + k) & (RING - 1u)] = src[k];
    return true;
}
static void save(const char *path, const void *data, size_t bytes)
{
    FILE *out = fopen(path, "wb");
    if (!out || fwrite(data, 1, bytes, out) != bytes || fclose(out)) exit(2);
}
int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    unsigned kp = (unsigned)atoi(argv[3]);
    if (kp >= KP_LEVELS) return 2;
    FILE *in = fopen(argv[1], "rb");
    if (!in || fseek(in, 0, SEEK_END)) return 2;
    long length = ftell(in);
    if (length < 2 || fseek(in, 0, SEEK_SET)) return 2;
    uint8_t *raw = malloc((size_t)length), *out = malloc((size_t)length);
    if (!raw || !out || fread(raw, 1, (size_t)length, in) != (size_t)length) return 2;
    fclose(in);
    uint8_t ring[RING] = {0}, workspace[1280];
    executor_t executor = {ring};
    sfw_ring_t r = {.ring = ring, .ring_bytes = RING, .phase = c5vrx_phase8_gain_lut,
        .verified_kp_x100 = kp_level_x100[kp], .program_verified = true,
        .decoder_lut = kp_tables[kp], .workspace = workspace, .workspace_bytes = sizeof(workspace),
        .commit = commit, .commit_ctx = &executor};
    sync_flywheel_t f;
    sfw_init(&f); /* No absence-source admission: no invented H/V clock. */
    uint64_t rx = 0, tx = 0, n = (uint64_t)length;
    while (tx < n) {
        uint64_t remaining = RING - (rx & (RING - 1u));
        uint64_t end = rx + (remaining < DSCR ? remaining : DSCR);
        if (end > n) end = n;
        for (; rx < end; ++rx) ring[rx & (RING - 1u)] = raw[rx];
        if (rx > DSCR) {
            uint64_t ceiling = rx - DSCR, tx_now = rx > LAG ? rx - LAG : 0;
            uint64_t local = tx_now & (RING - 1u), desc_end = (local / DSCR + 1u) * DSCR;
            if (desc_end > RING) desc_end = RING;
            sfw_run(&f, &r, ceiling, rx, tx_now - local + desc_end + 512u, true, 4000u);
        }
        uint64_t consume = rx > LAG ? rx - LAG : 0;
        if (rx == n) consume = n;
        for (; tx < consume; ++tx) out[tx] = ring[tx & (RING - 1u)];
    }
    save(argv[2], out, (size_t)n);
    uint16_t mapped[2][1024];
    unsigned clipped = 0;
    for (unsigned k = 0; k < 1024u; ++k) {
        bool clip;
        mapped[0][k] = output_entry(kp_tables[kp][k], 0u, &clip);
        if (mapped[0][k] != kp_tables[kp][k]) return 3;
        mapped[1][k] = output_entry(kp_tables[kp][k], 9u, &clip);
        clipped += clip;
        if ((mapped[1][k] & 0xffc0u) != (mapped[0][k] & 0xffc0u)) return 3;
    }
    char path[4096];
    if (snprintf(path, sizeof(path), "%s.lut", argv[2]) >= (int)sizeof(path)) return 2;
    save(path, mapped, sizeof(mapped));
    size_t changed = 0;
    for (uint64_t k = 0; k < n; ++k) changed += raw[k] != out[k];
    printf("{\"samples\":%llu,\"kp_x100\":%u,\"real_acquisitions\":%u,\"real_lock\":%s,"
        "\"real_v_fields\":%u,\"repaired_h\":%u,\"repaired_v\":%u,\"changed_iq\":%zu,"
        "\"half_lut_clipped\":%u,\"commit_refused\":%u,\"bootstrap_generated\":%u}\n",
        (unsigned long long)n, kp_level_x100[kp], f.acquisitions, sfw_locked(&f) ? "true" : "false",
        f.vsyncs, f.repaired, f.slots_repaired, changed, clipped, f.commit_refused, f.generated_starts);
    free(raw); free(out);
    return 0;
}
