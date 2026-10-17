/* C5VRX by Twotoz and contributors: provenance independently of sync success.
 * Synthetic bounds tests, not DMA-pointer, CPU deadline or LCD validation. */
#include <assert.h>
#include <stdio.h>
#include "sync_repair_ledger.h"

static void geometry(bool pal)
{
    sync_repair_ledger_t l;
    sync_repair_ledger_init(&l);
    const unsigned field = pal ? 625u : 525u, n = pal ? 5u : 6u;
    unsigned max_count = 0;
    for (unsigned h = 0; h < 12u * field; ++h) {
        uint64_t k = pal ? (uint64_t)h * 1280u : ((uint64_t)h * 22880u + 9u) / 18u;
        uint64_t oldest = k > 32768u ? k - 32768u : 0;
        assert(sync_repair_ledger_advance(&l, oldest));
        unsigned slot = h % field;
        if (slot < 3u * n || !(h & 1u)) {
            unsigned width = slot >= n && slot < 2u * n ? 1100u : 188u;
            assert(sync_repair_ledger_record(&l, k, k + width));
            assert(sync_repair_ledger_record(&l, k, k + width)); /* repeated retry */
            assert(!sync_repair_ledger_original(&l, k + 59u, k + 92u, k + 2000u));
        }
        if (l.count > max_count) max_count = l.count;
        assert(l.count <= 32u && l.collapses == 0);
    }
    printf("%s 12-field worst repair density: max=%u, no overflow/self-evidence\n",
           pal ? "PAL" : "NTSC", max_count);
}

static unsigned rng = 19;
static unsigned random_u(void) { rng = rng * 1664525u + 1013904223u; return rng; }

static void independent_byte_oracle(void)
{
    sync_repair_ledger_t l;
    sync_repair_ledger_init(&l);
    bool tainted[4096] = {0};
    uint64_t complete = 0;
    unsigned accepted = 0;
    for (unsigned iteration = 0; iteration < 1500; ++iteration) {
        uint64_t next = complete + 64u;
        for (uint64_t k = complete; k < next; ++k) tainted[k & 4095u] = false;
        complete = next;
        uint64_t oldest = complete > 4096u ? complete - 4096u : 0;
        assert(sync_repair_ledger_advance(&l, oldest));
        uint64_t a = oldest + random_u() % (complete - oldest);
        uint64_t b = a + 1u + random_u() % 24u;
        if (b > complete) b = complete;
        assert(sync_repair_ledger_record(&l, a, b));
        for (uint64_t k = a; k < b; ++k) tainted[k & 4095u] = true;
        for (unsigned query = 0; query < 80; ++query) {
            a = oldest + random_u() % (complete - oldest);
            b = a + 1u + random_u() % 12u;
            if (b > complete) b = complete;
            if (sync_repair_ledger_original(&l, a, b, complete)) {
                ++accepted;
                for (uint64_t k = a; k < b; ++k) assert(!tainted[k & 4095u]);
            }
        }
    }
    assert(accepted > 1000u);
    printf("1500 updates across ring wraps: %u accepted evidence spans independently unmodified\n", accepted);
}

int main(void)
{
    sync_repair_ledger_t l;
    sync_repair_ledger_init(&l);
    assert(sync_repair_ledger_record(&l, 100, 336)); /* H plus front porch */
    assert(sync_repair_ledger_original(&l, 90, 100, 1000));
    assert(!sync_repair_ledger_original(&l, 95, 105, 1000)); /* centred lowm reads */
    assert(!sync_repair_ledger_original(&l, 335, 337, 1000)); /* adjacent step */
    assert(sync_repair_ledger_original(&l, 336, 337, 1000));
    assert(!sync_repair_ledger_original(&l, 999, 1001, 1000));
    assert(!sync_repair_ledger_original(&l, UINT64_MAX - 5u, 5, 1000));
    assert(sync_repair_ledger_record(&l, 90, 110));
    assert(sync_repair_ledger_record(&l, 336, 350));
    assert(l.count == 1 && l.ranges[0].begin == 90 && l.ranges[0].end == 350);
    assert(sync_repair_ledger_advance(&l, 200));
    assert(!sync_repair_ledger_original(&l, 190, 200, 1000)); /* source overwritten */
    assert(!sync_repair_ledger_advance(&l, 199));
    assert(!sync_repair_ledger_record(&l, 199, 210));
    assert(sync_repair_ledger_advance(&l, 350) && l.count == 0);

    sync_repair_ledger_init(&l);
    for (unsigned i = 0; i < 65; ++i)
        assert(sync_repair_ledger_record(&l, 10u + 3u * i, 11u + 3u * i));
    assert(l.count == 1 && l.collapses == 1);
    for (unsigned i = 0; i < 65; ++i)
        assert(!sync_repair_ledger_original(&l, 10u + 3u * i, 11u + 3u * i, 1000));
    assert(!sync_repair_ledger_original(&l, 11, 12, 1000)); /* fail closed gap */
    assert(sync_repair_ledger_original(&l, 203, 204, 1000));
    assert(sync_repair_ledger_advance(&l, 203) && l.count == 0);
    geometry(true);
    geometry(false);
    independent_byte_oracle();
    puts("PASS: repair interval boundaries, replay/overlap, overwrite expiry, overflow fail-closed");
}
