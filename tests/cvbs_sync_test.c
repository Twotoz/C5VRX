#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "c5vrx_cvbs_sync.h"

static unsigned feed(c5vrx_cvbs_sync_tracker_t *tracker, uint8_t value,
                     unsigned count, c5vrx_cvbs_sync_event_t *last)
{
    unsigned events = 0;
    for (unsigned i = 0; i < count; ++i) {
        c5vrx_cvbs_sync_event_t event;
        if (c5vrx_cvbs_sync_consume(tracker, value, &event)) {
            ++events;
            *last = event;
        }
    }
    return events;
}

static void broad_sync(c5vrx_cvbs_sync_tracker_t *tracker,
                       c5vrx_cvbs_sync_event_t *last)
{
    assert(feed(tracker, 1u, 520u, last) == 0u);
    assert(feed(tracker, 20u, 20u, last) == 1u);
    assert(last->vertical);
}

static void line(c5vrx_cvbs_sync_tracker_t *tracker, unsigned period,
                 unsigned number, c5vrx_cvbs_sync_event_t *last)
{
    const unsigned pulse = 88u + number % 7u;
    (void)feed(tracker, number % 3u, pulse, last);
    (void)feed(tracker, 20u, 3u, last);
    /* Short low glitches in active video must not become line sync. */
    (void)feed(tracker, 38u, 200u, last);
    (void)feed(tracker, 2u, 4u, last);
    (void)feed(tracker, 45u, period - pulse - 3u - 204u, last);
}

int main(void)
{
    c5vrx_cvbs_sync_tracker_t tracker;
    c5vrx_cvbs_sync_event_t event = {0};
    c5vrx_cvbs_sync_init(&tracker);
    broad_sync(&tracker, &event);

    bool saw_lock = false;
    for (unsigned i = 0; i < 40u; ++i) {
        line(&tracker, 1280u, i, &event);
        if (event.horizontal && event.locked) saw_lock = true;
    }
    assert(saw_lock);
    assert(event.horizontal && event.locked);
    assert(event.field_line >= 30u);
    assert(event.line_period_samples >= 1270u);
    assert(event.line_period_samples <= 1290u);
    assert(c5vrx_cvbs_sync_threshold(&tracker) >= 4u);
    assert(c5vrx_cvbs_sync_threshold(&tracker) <= 14u);

    /* A second dirty field at the NTSC-ish line period must reacquire without
     * relying on a compile-time 1280-sample rollover. */
    broad_sync(&tracker, &event);
    saw_lock = false;
    for (unsigned i = 0; i < 40u; ++i) {
        line(&tracker, 1271u, i, &event);
        if (event.horizontal && event.locked) saw_lock = true;
    }
    assert(saw_lock);
    assert(event.line_period_samples >= 1265u);
    assert(event.line_period_samples <= 1280u);
    puts("cvbs_sync_test: PASS");
    return 0;
}
