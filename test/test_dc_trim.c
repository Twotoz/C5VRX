/* C5VRX by Twotoz/contributors: live DC trial acceptance decisions.
 * Covers only the pure helpers; the PHY trial/rollback path needs the board. */
#include <assert.h>
#include <stdio.h>
#include "dc_trim.h"

int main(void)
{
    dc_trim_context_t a = {83, 1, 2, 3, 4, 5865, 0, 1, 2, 1, 2, 650, 0x1234};
    dc_trim_context_t b = a;
    assert(dc_trim_same(&a, &b));
    b.gain_epoch++;
    assert(!dc_trim_same(&a, &b)); /* any own DC write invalidates the context */
    b = a; b.frequency = 5845;
    assert(!dc_trim_same(&a, &b));

    int big[2] = {900, 0}, half[2] = {450, 0}, same[2] = {899, 0}, worse[2] = {-950, 0};
    assert(dc_trim_cost(big) == 810000u);
    assert(dc_trim_improves(big, half));
    assert(!dc_trim_improves(big, same));   /* below absolute margin */
    assert(!dc_trim_improves(big, worse));  /* overshoot never accepted */
    assert(!dc_trim_improves(big, big));
    /* Near the deadband the 20000-mcell^2 absolute margin dominates. */
    int small[2] = {300, 0}, smaller[2] = {270, 0}, much[2] = {250, 0};
    assert(!dc_trim_improves(small, smaller));
    assert(dc_trim_improves(small, much));
    /* Both axes count; a gain on one axis cannot hide a loss on the other. */
    int diag[2] = {600, 600}, shifted[2] = {100, 800};
    assert(!dc_trim_improves(diag, shifted));
    int extreme[2] = {2000000000, -2000000000}, zero[2] = {0, 0};
    assert(dc_trim_improves(extreme, zero) && !dc_trim_improves(zero, extreme));
    assert(dc_trim_abs(-7) == 7 && dc_trim_abs(7) == 7);
    puts("PASS: DC trial context identity, two-axis cost, absolute/relative margin, overshoot and overflow");
    return 0;
}
