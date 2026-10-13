#include <assert.h>
#include <stdio.h>
#include "cn_observation.h"
int main(void)
{
    cn_observation_t o = {1, 2, 3, 4, 1000, 300};
    assert(cn_observation_fresh(&o, 2000, 1, 2, 3, 4));
    assert(cn_observation_fresh(&o, 41000, 1, 2, 3, 4));
    assert(!cn_observation_fresh(&o, 41001, 1, 2, 3, 4));
    assert(!cn_observation_fresh(&o, 999, 1, 2, 3, 4));
    assert(!cn_observation_fresh(&o, 2000, 5, 2, 3, 4));
    assert(!cn_observation_fresh(&o, 2000, 1, 5, 3, 4));
    assert(!cn_observation_fresh(&o, 2000, 1, 2, 5, 4));
    assert(!cn_observation_fresh(&o, 2000, 1, 2, 3, 5));
    o.us = 0;
    assert(!cn_observation_fresh(&o, 2000, 1, 2, 3, 4));
    puts("PASS: C/N cache age, gain/PHY/channel/table invalidation and missing/backward time");
    return 0;
}
