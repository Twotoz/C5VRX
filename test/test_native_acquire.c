#include <assert.h>
#include <stdio.h>
#include "native_acquire.h"
#include "arc_phy.h"
unsigned char phy_param[2048];
int main(void)
{
    arc_gain_table_t table;
    arc_gain_table_from_bytes(&table, NULL, ARC_5G_GAIN_MAX);
    for (unsigned g = 0; g <= table.max_index; ++g) {
        arc_gain_tuple_t t;
        uint8_t actual = 255;
        assert(arc_gain_tuple_decode(&table, (uint8_t)g, &t));
        assert(arc_gain_from_pbus(&table, t.rf_code, t.bb_code, 0xd0u | t.fine_code, &actual));
        assert(actual == g);
        for (unsigned prefix = 0; prefix <= 0x100u; prefix += 0x100u) {
            uint32_t packed = 0;
            assert(native_acquire_pack_pbus(t.rf_code, t.bb_code | prefix,
                0xd0u | t.fine_code, prefix, &packed));
            assert(packed == t.packed_state);
            assert(arc_gain_from_pbus(&table, packed >> 12, (packed >> 4) & 127u,
                0xd0u | (packed & 7u), &actual) && actual == g);
        }
    }
    for (unsigned prefix = 0; prefix <= 0x100u; prefix += 0x100u)
        for (unsigned bb = 0; bb < 1024u; ++bb) {
            uint32_t packed = 0xdeadbeef;
            bool valid = native_acquire_pack_pbus(0x1e7, bb, 0xd0, prefix, &packed);
            assert(valid == ((bb & ~127u) == prefix));
            assert(packed == (valid ? 0x1e7000u | ((bb & 127u) << 4) : 0xdeadbeef));
        }
    uint32_t packed = 0;
    assert(native_acquire_pack_pbus(0x1e7, 0x17f, 0xd0, 0x100, &packed));
    assert(packed == 0x1e77f0); /* actual G83 BW40 board readback */
    assert(!native_acquire_pack_pbus(512, 0x17f, 0xd0, 0x100, &packed));
    assert(!native_acquire_pack_pbus(0x1e7, 0x17f, 0x1d0, 0x100, &packed));
    assert(!native_acquire_pack_pbus(0x1e7, 0x17f, 0xd0, 0x180, &packed));
    assert(!native_acquire_pack_pbus(0x1e7, 0x17f, 0xd0, 0x100, NULL));
    uint8_t actual = 255;
    assert(!arc_gain_from_pbus(&table, 511, 3, 0xd0, &actual));
    assert(!arc_gain_from_pbus(&table, 24, 7, 0, &actual));
    assert(!arc_gain_from_pbus(&table, 24, 7, 0x1d0, &actual));
    assert(actual == 255);
    /* An ambiguous table cannot publish the first superficially matching row. */
    table.rf_code[1] = table.rf_code[0];
    table.start[1] = table.start[0];
    assert(!arc_gain_from_pbus(&table, 24, 7, 0xd5, &actual));
    native_acquire_t a = {0};
    assert(native_acquire_observe(&a, 0, false, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 1, true, true, 82) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 2, false, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 3, false, true, 39) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 4, false, true, 39) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 5, false, true, 39) == NATIVE_LANDING);
    a = (native_acquire_t){0};
    for (unsigned us = 0; us < NATIVE_TIMEOUT_US; ++us)
        assert(native_acquire_observe(&a, us, false, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 60, false, true, 40) == NATIVE_TIMEOUT);
    a = (native_acquire_t){0};
    assert(native_acquire_observe(&a, 3, true, true, 82) == NATIVE_GAP);
    a = (native_acquire_t){0};
    assert(native_acquire_observe(&a, 0, true, true, 82) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 1, false, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 2, false, false, 40) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 3, false, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 4, true, true, 82) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 5, false, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 6, false, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe(&a, 7, false, true, 40) == NATIVE_LANDING);
    a = (native_acquire_t){0};
    assert(native_acquire_observe_ticks(&a, 0, 240, true, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe_ticks(&a, 200, 240, false, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe_ticks(&a, 679, 240, false, true, 40) == NATIVE_WAIT);
    assert(native_acquire_observe_ticks(&a, 680, 240, false, true, 40) == NATIVE_LANDING);
    a = (native_acquire_t){0};
    assert(native_acquire_observe_ticks(&a, 481, 240, true, true, 40) == NATIVE_GAP);
    assert(native_acquire_observe_ticks(&a, 14400, 240, true, true, 40) == NATIVE_TIMEOUT);
    assert(native_acquire_observe_ticks(&a, 0, 0, true, true, 40) == NATIVE_TIMEOUT);
    assert(native_acquire_observe_ticks(&a, 0, 1001, true, true, 40) == NATIVE_TIMEOUT);
    puts("PASS: all 84 gains in both PBUS contexts, malformed/context-mismatch tuples, native start/landing/timeout/gap/restart");
    return 0;
}
