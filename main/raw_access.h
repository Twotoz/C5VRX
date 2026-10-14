/* C5VRX by Twotoz/contributors: task-owned raw reader/writer exclusion.
 * Pure state; caller serializes each operation. Not a DMA/frontier oracle,
 * and not proof of original IQ provenance after synthetic repairs. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#define RAW_ACCESS_READERS 8u
typedef struct { uint32_t stream, control, gain, phy; } raw_access_context_t;
typedef struct { raw_access_context_t context; uint64_t ticket; uint8_t slot; } raw_access_token_t;
typedef struct {
    uint64_t readers[RAW_ACCESS_READERS], writer, issued;
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
    bool valid=source_valid && raw_access_same(token->context,context);
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
    if (!token || !token->ticket || token->ticket!=s->writer) return false;
    s->writer=0; bool valid=source_valid && raw_access_same(token->context,context);
    token->ticket=0; return valid;
}
