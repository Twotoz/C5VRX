/* C5VRX by Twotoz/contributors: checked menu entry (PR #212 review
 * 2026-10-18). The flywheel observer/output must be released before any
 * menu DMA chain is built: a busy PHY used to skip the release silently and
 * the menu then allocated anyway (ESP_ERR_NO_MEM or overlapping owners).
 * Order: hold restarts -> checked release -> build -> start. A refused
 * release or a failed build leaves live video untouched and unwinds. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum { MENU_ENTRY_OPENED = 0, MENU_ENTRY_BUSY, MENU_ENTRY_FAILED } menu_entry_t;

typedef struct {
    void (*hold)(bool on);        /* stop the control owner restarting the flywheel */
    bool (*release)(void);        /* true once observer and output are gone */
    bool (*build)(void);          /* allocate and render the menu chain */
    void (*unwind)(void);         /* free any partial chain */
    void (*start)(void);          /* hand TX to the menu; live video stops here */
} menu_entry_ops_t;

static inline menu_entry_t menu_entry_open(const menu_entry_ops_t *o)
{
    o->hold(true);
    if (!o->release()) { o->hold(false); return MENU_ENTRY_BUSY; }
    if (!o->build()) { o->unwind(); o->hold(false); return MENU_ENTRY_FAILED; }
    o->start();
    o->hold(false); /* the active menu itself keeps the flywheel stopped */
    return MENU_ENTRY_OPENED;
}

/* Long press while the PHY is owned elsewhere: retry from the control
 * owner for a bounded time, never block, never allocate before release. */
#define MENU_ENTRY_RETRY_US 1000000
typedef struct { bool pending; int64_t deadline_us; uint32_t deferred, refused; } menu_retry_t;

static inline void menu_retry_busy(menu_retry_t *r, int64_t now)
{
    if (!r->pending) { r->pending = true; r->deadline_us = now + MENU_ENTRY_RETRY_US; }
    ++r->deferred;
}
/* true: try again now; false: nothing pending, or the deadline passed
 * (then counted as refused and cleared). */
static inline bool menu_retry_due(menu_retry_t *r, int64_t now)
{
    if (!r->pending) return false;
    if (now >= r->deadline_us) { r->pending = false; ++r->refused; return false; }
    return true;
}
static inline void menu_retry_done(menu_retry_t *r) { r->pending = false; }
