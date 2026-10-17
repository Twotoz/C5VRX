/* C5VRX by Twotoz/contributors: task-owned raw reader/writer exclusion.
 * Pure state; caller serializes each operation. Not a DMA/frontier oracle,
 * and not proof of original IQ provenance after synthetic repairs. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#define RAW_ACCESS_READERS 8u
typedef struct { uint32_t stream, control, gain, phy; } raw_access_context_t;
typedef struct { raw_access_context_t context; uint64_t ticket; uint8_t slot; } raw_access_token_t;

typedef struct {
    raw_access_context_t context;
    uint64_t writer_ticket, resumed_ticket;
    uint8_t slot;
} raw_access_conversion_t;
typedef struct {
    uint64_t readers[RAW_ACCESS_READERS], writer, issued;
    raw_access_context_t reader_contexts[RAW_ACCESS_READERS];
    raw_access_conversion_t *conversion;
    uint64_t reserved_reader;
    uint8_t conversion_slot;
} raw_access_t;
static inline bool raw_access_same(raw_access_context_t a, raw_access_context_t b)
{
    return a.stream==b.stream && a.control==b.control && a.gain==b.gain && a.phy==b.phy;
}
static inline unsigned raw_access_readers(const raw_access_t *s)
{
    unsigned n=0; for (unsigned k=0;k<RAW_ACCESS_READERS;++k) n+=s->readers[k]!=0; return n;
}
static inline bool raw_access_read_begin(raw_access_t *s, raw_access_context_t context,
                                          bool source_valid, raw_access_token_t *out)
{
    if (!out) return false;
    *out=(raw_access_token_t){0};
    if (!source_valid || s->writer || s->issued==UINT64_MAX) return false;
    for (unsigned k=0;k<RAW_ACCESS_READERS;++k) if (!s->readers[k]) {
        uint64_t ticket=++s->issued; s->readers[k]=ticket;
        s->reader_contexts[k]=context;
        *out=(raw_access_token_t){context,ticket,(uint8_t)k}; return true;
    }
    return false;
}
static inline bool raw_access_read_end(raw_access_t *s, raw_access_token_t *token,
                                        raw_access_context_t context, bool source_valid)
{
    if (!token || !token->ticket || token->slot>=RAW_ACCESS_READERS || s->readers[token->slot]!=token->ticket) return false;
    /* Release even if RX restarted or the source changed while preempted.
     * Never reset active slots on a generation/context transition. */
    s->readers[token->slot]=0;
    bool valid=source_valid && raw_access_same(token->context,context) &&
        raw_access_same(token->context,s->reader_contexts[token->slot]);
    token->ticket=0; return valid;
}
/* No firmware writer invokes these yet. Actual future commit also needs
 * physical frontier/publication/WCET proof and repair-ledger admission. */
static inline bool raw_access_write_begin(raw_access_t *s, raw_access_context_t context,
                                           bool source_valid, raw_access_token_t *out)
{
    if (!out) return false;
    *out=(raw_access_token_t){0};
    if (!source_valid || s->writer || raw_access_readers(s) || s->issued==UINT64_MAX) return false;
    s->writer=++s->issued; *out=(raw_access_token_t){context,s->writer,0}; return true;
}
static inline bool raw_access_write_end(raw_access_t *s, raw_access_token_t *token,
                                         raw_access_context_t context, bool source_valid)
{
    if (!token || !token->ticket || token->ticket!=s->writer || s->conversion) return false;
    s->writer=0; bool valid=source_valid && raw_access_same(token->context,context);
    token->ticket=0; return valid;
}

/* These operations transfer CPU ownership only, never DMA write permission.
 * Caller serializes EACH operation and initializes handle to zero. The active
 * handle must stay at the same address, disjoint from manager/token storage.
 * Upgrade retains the original READ lease on EVERY refusal. On success the
 * caller MUST downgrade on every exit; ordinary write_end cannot release it.
 * Downgrade always restores the reserved reader after valid handle admission,
 * even when returning false for source/context validity. Close that output
 * reader normally, including on failure. No allocation or ticket issue occurs
 * during downgrade; reserved tickets cannot be stolen by intervening callers. */
static inline bool raw_access_disjoint(const void *a, uintptr_t an,
                                       const void *b, uintptr_t bn)
{
    uintptr_t x=(uintptr_t)a,y=(uintptr_t)b;
    if (an>UINTPTR_MAX-x || bn>UINTPTR_MAX-y) return false;
    return x+an<=y || y+bn<=x;
}
static inline bool raw_access_read_upgrade(raw_access_t *s, raw_access_token_t *read,
    raw_access_context_t context, bool source_valid, raw_access_conversion_t *handle)
{
    if (!s || !read || !handle ||
        !raw_access_disjoint(s,sizeof(*s),read,sizeof(*read)) ||
        !raw_access_disjoint(s,sizeof(*s),handle,sizeof(*handle)) ||
        !raw_access_disjoint(read,sizeof(*read),handle,sizeof(*handle))) return false;
    if (!source_valid || s->writer || s->conversion || handle->writer_ticket ||
        handle->resumed_ticket || !read->ticket || read->slot>=RAW_ACCESS_READERS ||
        s->readers[read->slot]!=read->ticket ||
        !raw_access_same(read->context,context) ||
        !raw_access_same(read->context,s->reader_contexts[read->slot]) ||
        raw_access_readers(s)!=1u || s->issued>UINT64_MAX-2u) return false;
    uint64_t writer=s->issued+1u,resumed=s->issued+2u;
    s->issued=resumed;s->writer=writer;s->reserved_reader=resumed;
    s->conversion_slot=read->slot;s->conversion=handle;s->readers[read->slot]=0;
    *handle=(raw_access_conversion_t){read->context,writer,resumed,read->slot};
    read->ticket=0;
    return true;
}
static inline bool raw_access_read_downgrade(raw_access_t *s, raw_access_conversion_t *handle,
    raw_access_context_t context, bool source_valid, raw_access_token_t *out)
{
    if (!s || !handle || !out ||
        !raw_access_disjoint(s,sizeof(*s),handle,sizeof(*handle)) ||
        !raw_access_disjoint(s,sizeof(*s),out,sizeof(*out)) ||
        !raw_access_disjoint(handle,sizeof(*handle),out,sizeof(*out))) return false;
    if (s->conversion!=handle || !s->writer || handle->writer_ticket!=s->writer ||
        !s->reserved_reader || handle->resumed_ticket!=s->reserved_reader ||
        handle->slot!=s->conversion_slot || handle->slot>=RAW_ACCESS_READERS ||
        s->readers[handle->slot] || raw_access_readers(s) ||
        !raw_access_same(handle->context,s->reader_contexts[handle->slot])) return false;
    bool valid=source_valid && raw_access_same(handle->context,context);
    *out=(raw_access_token_t){handle->context,s->reserved_reader,handle->slot};
    s->readers[handle->slot]=s->reserved_reader;
    s->writer=0;s->reserved_reader=0;s->conversion=NULL;s->conversion_slot=0;
    *handle=(raw_access_conversion_t){0};
    return valid;
}
