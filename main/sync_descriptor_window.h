/* C5VRX by Twotoz/contributors: whole-descriptor GDMA memory window.
 * Engineering model from C5 TRM1.1 current/next-not-processed + DONE contracts.
 * No writer caller. IRQ/cache publication WCET is a caller-supplied bound,
 * not established by this arithmetic or by a status/proof boolean. */
#pragma once
#include "sync_memory_frontier.h"
#include "raw_access.h"
#define SDW_MAX_PULSE 1280u
#define SDW_MEMORY_BYTES_PER_US 192u
typedef enum {
    SDW_OK, SDW_NOT_READY, SDW_LAYOUT, SDW_CONTEXT, SDW_REGISTERS, SDW_CLOCK,
    SDW_TIME, SDW_AMBIGUOUS, SDW_OVERFLOW, SDW_FRESHNESS, SDW_LENGTH, SDW_WINDOW
} sdw_result_t;
typedef struct {
    dma_progress_layout_t rx, tx;
    uint32_t generation, epoch, history_bytes;
    bool ready;
} sdw_model_t;
typedef struct {
    sync_memory_registers_t first, last;
    raw_access_context_t context;
    uint32_t epoch;
    uint64_t rx_current, tx_current, fresh_from, first_us, last_us;
    /* Independently unwrapped completed-data ends. BF0/current is pre-read
     * metadata and cannot anchor the CPU reader's protected history. */
    uint64_t rx_done_end, tx_done_end;
} sdw_capture_t;
typedef struct {
    uint64_t safe_first, safe_last, cache_first, cache_last, deadline_us, motion_bytes;
} sdw_window_t;
static inline sdw_result_t sdw_init(sdw_model_t *m, const dma_progress_layout_t *rx,
    const dma_progress_layout_t *tx, uint32_t generation, uint32_t epoch)
{
    if (!m) return SDW_NOT_READY;
    memset(m,0,sizeof(*m));
    if (!generation || !epoch) return SDW_CONTEXT;
    if (!rx || !tx || !dma_progress_layout_valid(rx) || !dma_progress_layout_valid(tx)) return SDW_LAYOUT;
    uint32_t lengths[RAW_HISTORY_NODES];
    for (unsigned i=0;i<RAW_HISTORY_NODES;++i) lengths[i]=rx->nodes[i].bytes;
    raw_history_layout_t h=raw_history_layout(lengths,rx->nodes[0].offset);
    if (!h.valid) return SDW_LAYOUT;
    m->rx=*rx; m->tx=*tx; m->generation=generation; m->epoch=epoch;
    m->history_bytes=h.protected_bytes; m->ready=true; return SDW_OK;
}
/* now_us is sampled before admission. commit_us must cover the remaining
 * admission work as well as all stores, cache maintenance and final ordering
 * through complete publication. A caller that cannot bound admission work
 * must revalidate the deadline immediately before the first store.
 * Absolute starts come from an independently unwrapped single data epoch;
 * this function cannot reconstruct laps from modulo descriptor addresses. */
static inline sdw_result_t sdw_admit(const sdw_model_t *m, const sdw_capture_t *c,
    raw_access_context_t current, uint32_t epoch, uint64_t now_us, uint32_t commit_us,
    uint64_t first, uint64_t last, sdw_window_t *out)
{
    if (!out) return SDW_NOT_READY;
    *out=(sdw_window_t){0};
    if (!m || !m->ready || !c) return SDW_NOT_READY;
    if (!raw_access_same(c->context,current) || current.stream!=m->generation ||
        epoch!=m->epoch || c->epoch!=epoch) return SDW_CONTEXT;
    if (first>=last || last-first>SDW_MAX_PULSE || last>UINT64_MAX-63u) return SDW_LENGTH;
    if (!sync_memory_same(&c->first,&c->last)) return SDW_REGISTERS;
    uint32_t ahb_hz;
    if (!sync_memory_clock(&c->last,&ahb_hz)) return SDW_CLOCK;
    int ri=dma_progress_index(&m->rx,c->last.rx_bf0), rd=dma_progress_index(&m->rx,c->last.rx_done);
    int ti=dma_progress_index(&m->tx,c->last.tx_bf0), td=dma_progress_index(&m->tx,c->last.tx_done);
    int tn=dma_progress_index(&m->tx,c->last.tx_next);
    if (ri<0 || rd<0 || ti<0 || td<0 || tn<0 || m->tx.nodes[ti].next!=c->last.tx_next ||
        (c->last.tx_state&0x3ffffu)!=(c->last.tx_next&0x3ffffu) ||
        c->rx_current%RAW_HISTORY_RING!=m->rx.nodes[ri].offset ||
        c->tx_current%RAW_HISTORY_RING!=m->tx.nodes[ti].offset) return SDW_REGISTERS;
    unsigned r0=((unsigned)rd+1u)%RAW_HISTORY_NODES, r1=(r0+1u)%RAW_HISTORY_NODES;
    unsigned t0=((unsigned)td+1u)%RAW_HISTORY_NODES, t1=(t0+1u)%RAW_HISTORY_NODES;
    if (((unsigned)ri!=r0 && (unsigned)ri!=r1) ||
        ((unsigned)ti!=t0 && (unsigned)ti!=t1) ||
        c->rx_done_end%RAW_HISTORY_RING!=m->rx.nodes[r0].offset ||
        c->tx_done_end%RAW_HISTORY_RING!=m->tx.nodes[t0].offset)
        return SDW_REGISTERS;
    uint32_t rx_skip=(unsigned)ri==r1?m->rx.nodes[r0].bytes:0;
    uint32_t tx_skip=(unsigned)ti==t1?m->tx.nodes[t0].bytes:0;
    if (c->rx_done_end>UINT64_MAX-rx_skip || c->tx_done_end>UINT64_MAX-tx_skip)
        return SDW_OVERFLOW;
    if (c->rx_current!=c->rx_done_end+rx_skip ||
        c->tx_current!=c->tx_done_end+tx_skip) return SDW_REGISTERS;
    if (!commit_us || !c->first_us || c->last_us<c->first_us || now_us<c->last_us || now_us>UINT64_MAX-commit_us) return SDW_TIME;
    uint64_t deadline=now_us+commit_us;
    if (deadline==UINT64_MAX) return SDW_OVERFLOW;
    uint64_t elapsed=deadline-c->first_us+1u; /* include timer quantization */
    if (elapsed>UINT64_MAX/SDW_MEMORY_BYTES_PER_US) return SDW_OVERFLOW;
    uint64_t motion=elapsed*SDW_MEMORY_BYTES_PER_US;
    /* Initial current-descriptor uncertainty participates in the hidden-lap
     * exclusion. Equal tuples do not rule out an entire unobserved lap. */
    uint32_t rx_bytes=m->rx.nodes[ri].bytes, tx_bytes=m->tx.nodes[ti].bytes;
    unsigned rn=((unsigned)ri+1u)%RAW_HISTORY_NODES;
    uint64_t rx_cover=(uint64_t)rx_skip+rx_bytes+m->rx.nodes[rn].bytes;
    uint64_t tx_cover=(uint64_t)tx_skip+tx_bytes+m->tx.nodes[tn].bytes;
    uint64_t max_cover=rx_cover>tx_cover?rx_cover:tx_cover;
    if (motion>=RAW_HISTORY_RING-max_cover) return SDW_AMBIGUOUS;
    if (c->rx_done_end>UINT64_MAX-rx_cover-motion || c->tx_done_end>UINT64_MAX-tx_cover-motion) return SDW_OVERFLOW;
    if (c->rx_done_end<c->tx_done_end || c->rx_done_end-c->tx_done_end>=RAW_HISTORY_RING-m->rx.nodes[r0].bytes ||
        !c->fresh_from || c->rx_done_end<c->fresh_from || c->rx_done_end-c->fresh_from<m->history_bytes) return SDW_FRESHNESS;
    uint64_t rx_upper=c->rx_done_end+rx_cover+motion, tx_upper=c->tx_done_end+tx_cover+motion;
    uint64_t lower=rx_upper>=RAW_HISTORY_RING ? rx_upper-RAW_HISTORY_RING : 0;
    if (lower<c->fresh_from) lower=c->fresh_from;
    if (lower<tx_upper) lower=tx_upper;
    if (lower>UINT64_MAX-63u) return SDW_OVERFLOW;
    uint64_t safe_first=(lower+63u)&~63ull;
    uint64_t safe_last=(c->rx_done_end-m->history_bytes)&~63ull;
    uint64_t a=first&~63ull,b=(last+63u)&~63ull;
    *out=(sdw_window_t){safe_first,safe_last,a,b,deadline,motion};
    return safe_first<safe_last && a>=safe_first && b<=safe_last ? SDW_OK : SDW_WINDOW;
}
