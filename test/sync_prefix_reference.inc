/* C5VRX by Twotoz/contributors. Frozen reference from cce2eb7 main/sync_flywheel.c.
 * Only private symbol names are changed; retain old y evolution and quadratic dedup. */
static uint16_t baseline_value(const uint16_t *lut, unsigned state, uint8_t a, uint8_t b)
{
    unsigned address = (a >> 4) | ((a & 15u) << 4) | ((b & 128u) << 1) | ((b & 8u) << 6);
    return lut[state * 32u + (lut[address] >> 11)];
}

/* A first V equalizing pulse has no spare received front porch. Recover
 * the exact surviving tracker-state set through a bounded transmitted IQ
 * prefix. If states have not merged, choose against their worst case rather
 * than inventing one exact phase. Modified bytes are appropriate
 * here: this predicts decoder state, and is never camera-sync evidence.
 * Both pair parities must admit the same generated waveform. */
typedef struct { unsigned phase, x, y; } baseline_bound_t;

static void baseline_advance(const uint16_t *lut, baseline_bound_t *bound, uint8_t raw, uint8_t mid)
{
    unsigned address = (raw >> 4) | ((raw & 15u) << 4) | ((mid & 128u) << 1) | ((mid & 8u) << 6);
    unsigned carry = ((address >> 8) + (bound->phase >> 3)) >> 2;
    bound->y = (bound->y >> 1) + (bound->x >> 1) + carry;
    unsigned value = baseline_value(lut, bound->phase, raw, mid);
    bound->phase = (value >> 6) & 31u;
    bound->x = value & 63u;
}

static bool baseline_prefix(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t a,
                           baseline_bound_t states[2][32], unsigned counts[2], uint64_t last[2])
{
    const uint32_t mask = r->ring_bytes - 1u;
    if (!r->decoder_lut || a < 130u || a - 130u < f->repairs.oldest_readable) return false;
    for (unsigned parity = 0; parity < 2; ++parity) {
        last[parity] = a - 2u;
        if ((last[parity] & 1u) != parity) --last[parity];
        unsigned count = 32;
        for (unsigned j = 0; j < count; ++j) states[parity][j] = (baseline_bound_t){j, 63, 63};
        for (uint64_t k = last[parity] - 126u; k <= last[parity]; k += 2u) {
            for (unsigned j = 0; j < count; ++j)
                baseline_advance(r->decoder_lut, &states[parity][j],
                    r->ring[(uint32_t)k & mask], r->ring[(uint32_t)(k + 1u) & mask]);
            unsigned kept = 0;
            for (unsigned j = 0; j < count; ++j) {
                unsigned dst = 0;
                for (; dst < kept; ++dst)
                    if (states[parity][dst].phase == states[parity][j].phase &&
                        states[parity][dst].x == states[parity][j].x) break;
                if (dst == kept) states[parity][kept++] = states[parity][j];
                else if (states[parity][j].y > states[parity][dst].y)
                    states[parity][dst].y = states[parity][j].y;
            }
            count = kept;
        }
        counts[parity] = count;
        if (count != 1) ++f->bootstrap_unknown;
    }
    return true;
}

/* Only the first EQ has no preceding synthetic porch. Use the actual
 * worker table to make its tip directly, against every surviving decoder
 * state. This needs at most 28 regular observation tokens per byte, not a
 * 256-phase carrier search. The remaining slots use the ordinary DDA. */
static bool baseline_tip(sync_flywheel_t *f, const sfw_ring_t *r, uint64_t a,
                        unsigned low, uint8_t *tip)
{
    baseline_bound_t states[2][32];
    unsigned counts[2];
    uint64_t last[2];
    uint8_t cells[28];
    uint32_t available = 0;
    if (!baseline_prefix(f, r, a, states, counts, last)) return false;
    for (unsigned phase = 0; phase < 256u; ++phase) {
        uint8_t raw = s_cell[phase];
        unsigned address = (raw >> 4) | ((raw & 15u) << 4);
        unsigned token = r->decoder_lut[address] >> 11;
        if (token >= 28u) continue;
        /* Strong-cell tokens must be independent of the middle byte. */
        bool regular = true;
        for (unsigned quadrant = 1; quadrant < 4u; ++quadrant)
            if ((r->decoder_lut[address | (quadrant << 8)] >> 11) != token) regular = false;
        if (!regular) continue;
        if (!(available & (1u << token))) cells[token] = raw;
        available |= 1u << token;
    }
    unsigned previous_phase = ph_at(r, a - 1u);
    for (unsigned k = 0; k < low; ++k) {
        unsigned parity = (unsigned)((a + k) & 1u);
        unsigned best = UINT32_MAX, worst_best = UINT32_MAX;
        uint8_t chosen = 0;
        for (unsigned token = 0; token < 28u; ++token) if (available & (1u << token)) {
            unsigned worst = 0, sum = 0;
            for (unsigned j = 0; j < counts[parity]; ++j) {
                unsigned code = r->decoder_lut[states[parity][j].phase * 32u + token] & 63u;
                if (code > worst) worst = code;
                sum += code;
            }
            int target = (int)previous_phase + f->repair_step_q8 / 256;
            int error = wrap8((int)r->phase[cells[token]] - target);
            if (error < 0) error = -error;
            unsigned score = (worst << 20) + (sum << 8) + (unsigned)error;
            if (score < best) { best = score; worst_best = worst; chosen = cells[token]; }
        }
        if (worst_best > 8u) return false;
        tip[k] = chosen;
        unsigned other = parity ^ 1u;
        uint8_t endpoint = k ? tip[k - 1u] : r->ring[(uint32_t)(a - 1u) & (r->ring_bytes - 1u)];
        for (unsigned j = 0; j < counts[other]; ++j)
            baseline_advance(r->decoder_lut, &states[other][j], endpoint, chosen);
        previous_phase = r->phase[chosen];
    }
    return true;
}

