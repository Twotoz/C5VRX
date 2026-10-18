/* C5VRX by Twotoz/contributors: checked menu entry and bounded retry. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "menu_entry.h"

static char trace[64];
static bool release_ok, build_ok, held, flywheel_active;
static void add(char c) { size_t n = strlen(trace); trace[n] = c; trace[n + 1] = 0; }
static void hold(bool on) { held = on; add(on ? 'H' : 'h'); }
static bool release(void)
{
    add('R');
    if (release_ok) flywheel_active = false;
    return release_ok;
}
static bool build(void) { assert(!flywheel_active); add('B'); return build_ok; } /* never next to a live owner */
static void unwind(void) { add('U'); }
static void start(void) { add('S'); }
static const menu_entry_ops_t ops = {hold, release, build, unwind, start};

static menu_entry_t run(bool r, bool b)
{
    trace[0] = 0; release_ok = r; build_ok = b; flywheel_active = true;
    return menu_entry_open(&ops);
}

int main(void)
{
    unsigned checks = 0;
    /* PHY busy on menu entry: no allocation, restarts allowed again, live kept. */
    assert(run(false, true) == MENU_ENTRY_BUSY && !strcmp(trace, "HRh") && !held); ++checks;
    /* Allocation fails after a good release: partial chain unwound, no start. */
    assert(run(true, false) == MENU_ENTRY_FAILED && !strcmp(trace, "HRBUh") && !held); ++checks;
    /* Normal entry: hold, release, build, start, hold dropped (menu blocks restarts). */
    assert(run(true, true) == MENU_ENTRY_OPENED && !strcmp(trace, "HRBSh") && !held); ++checks;

    /* Bounded retry from the control owner. */
    menu_retry_t r = {0};
    assert(!menu_retry_due(&r, 0)); ++checks;                     /* nothing pending */
    menu_retry_busy(&r, 1000);
    assert(r.pending && r.deferred == 1u && menu_retry_due(&r, 2000)); ++checks;
    menu_retry_busy(&r, 5000);                                     /* deadline not extended */
    assert(r.deadline_us == 1000 + MENU_ENTRY_RETRY_US && r.deferred == 2u); ++checks;
    assert(!menu_retry_due(&r, 1000 + MENU_ENTRY_RETRY_US) && !r.pending && r.refused == 1u); ++checks;
    menu_retry_busy(&r, 9000000);
    menu_retry_done(&r);
    assert(!r.pending && !menu_retry_due(&r, 9000001) && r.refused == 1u); ++checks; /* opened in time */
    printf("PASS: %u menu entry checks\n", checks);
    return 0;
}
