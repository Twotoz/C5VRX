/* C5VRX by Twotoz/contributors: actual short-tail DMA geometry and safety. */
#include <stdio.h>
#include "control_window.h"
int main(void)
{
    const size_t lengths[9] = {4092,4092,4092,4092,4092,4092,4092,4092,32};
    unsigned checks = 0, failures = 0;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL line %d\n", __LINE__); } } while (0)
    for (unsigned active = 0; active < 9; ++active) {
        control_copy_plan_t plan = {0};
        CHECK(control_copy_plan(lengths, 9, active, 4092, &plan));
        CHECK(plan.index != active && plan.index != 8 && lengths[plan.index] >= 4092);
        CHECK(plan.index == (active == 0 ? 7 : active - 1));
        size_t expected = 0;
        for (unsigned step = 1; step < plan.distance; ++step) expected += lengths[(active + step) % 9];
        CHECK(plan.safe_bytes == expected);
        CHECK(control_copy_current(&plan, 9, active, active, 1, 40000000));
        CHECK(!control_copy_current(&plan, 9, active, plan.index, 1, 40000000));
        CHECK(!control_copy_current(&plan, 9, active, active, 1000, 40000000));
        for (unsigned step = 0; step < 9; ++step)
            CHECK(control_copy_current(&plan, 9, active, (active + step) % 9, 1, 40000000) == (step < plan.distance));
    }
    control_copy_plan_t p = {0};
    CHECK(control_copy_plan(lengths, 9, 0, 4092, &p));
    CHECK(p.safe_bytes == 6u * 4092u && p.distance == 7);
    CHECK(control_copy_current(&p, 9, 0, 0, 612, 40000000));
    CHECK(!control_copy_current(&p, 9, 0, 0, 613, 40000000));
    CHECK(!control_copy_plan(lengths, 9, 0, 4093, &p));
    CHECK(!control_copy_plan(lengths, 2, 0, 64, &p));
    CHECK(!control_copy_plan(lengths, 9, 9, 64, &p));
    printf("%s: %u checks, %u failures\n", failures ? "FAIL" : "PASS", checks, failures);
    return failures != 0;
}
