/* C5VRX-5 by Twotoz and the C5VRX contributors: bounded field patches.
 * Footprints from sense_lab.c suites 2/d/f/g/h (PR #254/#292). No claim
 * that CURRENT is hardware current or LOOP CAP is an RX noise control.
 * Caller holds PHY ownership. Restore exact entry words BEFORE vendor
 * reconfiguration, then capture its new stock and reapply the selection. */
#include "experimental.h"
#include "phy.h"
#include "rf.h"
#include "esp_system.h"
#include "esp_timer.h"
#include <string.h>

#ifdef C5VRX_PHY_PINNED
extern uint8_t phy_i2c_readReg(uint8_t block, uint8_t host, uint8_t reg);
extern void phy_i2c_writeReg(uint8_t block, uint8_t host, uint8_t reg, uint8_t value);
extern uint16_t phy_pbus_rd(uint32_t block, uint32_t bank);
extern uint8_t phy_param[];
extern void phy_current_level_set(uint8_t level);
#define REG(a) (*(volatile uint32_t *)(uintptr_t)(a))
enum { MMIO, I2C, CURRENT, PARAM, PBUS };
typedef struct { unsigned kind; uint32_t addr, mask, value, stock; uint8_t block, bank, reg; } patch_t;
typedef struct { bool active; unsigned n; patch_t p[4]; } patch_group_t;
static patch_group_t s_groups[5];
static int group_index(unsigned id)
{
    return id == EXP_IQ_CORR ? 0 : id >= EXP_LNA_BIAS && id <= EXP_SPUR0 ? 1 + (int)(id - EXP_LNA_BIAS) : -1;
}
static bool pbus_idle(void)
{
    const int64_t until = esp_timer_get_time() + 1000;
    while (REG(0x600a0890) & 0x80000000u) if (esp_timer_get_time() >= until) return false;
    return true;
}
static bool read_patch(const patch_t *p, uint32_t *v)
{
    if (p->kind == PBUS && !pbus_idle()) return false;
    *v = (p->kind == MMIO ? REG(p->addr) : p->kind == I2C ? phy_i2c_readReg(p->block, p->bank, p->reg) :
          p->kind == CURRENT ? phy_param[41] : p->kind == PARAM ? phy_param[p->addr] : phy_pbus_rd(p->block, p->bank)) & p->mask;
    return true;
}
static bool write_patch(const patch_t *p, uint32_t v)
{
    if (p->kind == MMIO) REG(p->addr) = (REG(p->addr) & ~p->mask) | v;
    else if (p->kind == I2C) {
        const uint8_t old = phy_i2c_readReg(p->block, p->bank, p->reg);
        phy_i2c_writeReg(p->block, p->bank, p->reg, (uint8_t)((old & ~p->mask) | v));
    }
    else if (p->kind == CURRENT) phy_current_level_set((uint8_t)v);
    else if (p->kind == PARAM) phy_param[p->addr] = (uint8_t)((phy_param[p->addr] & ~p->mask) | v);
    else {
        if (!pbus_idle()) return false;
        const uint32_t command = REG(0x600a0884);
        if (!(command & 1u) || (command & 2u)) return false;
        const uint32_t word = (phy_pbus_rd(p->block, p->bank) & ~p->mask) | v;
        REG(0x600a0884) = (command & 0xfffe0001u) |
            (((word << 6) | ((uint32_t)p->block << 2) | ((uint32_t)p->bank << 15)) & 0x1fffcu) | 2u;
        if (!pbus_idle()) return false; /* unknown transaction: caller fails closed */
        REG(0x600a0884) = command;
        if (REG(0x600a0884) != command) return false;
    }
    uint32_t got;
    return read_patch(p, &got) && got == v;
}
static void restore(patch_group_t *g)
{
    if (!g->active) return;
    for (unsigned i = g->n; i; --i) if (!write_patch(&g->p[i - 1], g->p[i - 1].stock)) esp_restart();
    g->active = false;
}
void experimental_phy_prepare_restore(void)
{
    for (unsigned i = 5; i; --i) restore(&s_groups[i - 1]);
}
static patch_t mmio(uint32_t addr, uint32_t mask, uint32_t value)
{ return (patch_t){.kind=MMIO, .addr=addr, .mask=mask, .value=value}; }
bool experimental_phy_option(unsigned id, uint8_t value)
{
    const int index = group_index(id);
    if (index < 0) return false;
    patch_group_t *g = &s_groups[index];
    restore(g);
    if (!value) return true;
    memset(g, 0, sizeof(*g));
    if (id == EXP_IQ_CORR) {
        g->n = 2;
        g->p[0] = mmio(0x600a0438, 0xe0000000u, 0);
        g->p[1] = mmio(0x600a0c0c, 0x6000, 0);
    } else if (id == EXP_LNA_BIAS) {
        if (phy_i2c_readReg(0x6a, 1, 0) != 0xcf || phy_i2c_readReg(0x6a, 1, 1) != 0x7f) return false;
        g->n = 1;
        g->p[0] = (patch_t){.kind=I2C, .block=0x6a, .bank=1, .reg=0, .mask=0xf0, .value=0x90};
    } else if (id == EXP_CURRENT) {
        static const uint8_t levels[] = {0, 0, 15, 255};
        g->n = 1;
        g->p[0] = (patch_t){.kind=CURRENT, .mask=0xff, .value=levels[value]};
    } else if (id == EXP_LOOP_CAP) {
        if (!phy_param[42] || !(REG(0x600a0884) & 1u) || (REG(0x600a0884) & 2u) || !pbus_idle()) return false;
        const unsigned bin = value - 1;
        static const uint8_t adj[] = {252, 253, 4, 4, 4, 4, 4};
        const uint8_t a = phy_param[0x2d2 + 2 * bin], b = phy_param[0x2d3 + 2 * bin];
        if (a > 31 || b > 15) return false;
        unsigned adjusted = (uint8_t)(b + adj[bin]);
        if (adjusted < 1) adjusted = 1;
        if (adjusted > 14) adjusted = 14;
        g->n = 3;
        g->p[0] = (patch_t){.kind=PARAM, .addr=690, .mask=0xff, .value=8};
        g->p[1] = (patch_t){.kind=PBUS, .block=9, .bank=2, .mask=0x1ff, .value=((uint32_t)a << 4) | 8};
        g->p[2] = (patch_t){.kind=PBUS, .block=10, .bank=2, .mask=0x1ff, .value=adjusted};
    } else {
        const uint32_t ctrl = REG(0x600a7c14);
        if (ctrl & 0x2000u) return false; /* preserve a vendor-owned active slot */
        g->n = 4;
        g->p[0] = mmio(0x600a7810, UINT32_MAX, 0x2febfafe);
        g->p[1] = mmio(0x600a7814, UINT32_MAX, 0x0865dbae);
        g->p[2] = mmio(0x600a7830, 0x7f, value == 1 ? 0x10 : 0x70);
        g->p[3] = mmio(0x600a7c14, 0x3fff, value == 1 ? 0x2200 : 0x3e00);
    }
    for (unsigned i = 0; i < g->n; ++i) if (!read_patch(&g->p[i], &g->p[i].stock)) return false;
    g->active = true;
    for (unsigned i = 0; i < g->n; ++i) if (!write_patch(&g->p[i], g->p[i].value)) { restore(g); return false; }
    return true;
}
#else
void experimental_phy_prepare_restore(void) {}
bool experimental_phy_option(unsigned item, uint8_t value) { (void)item; return value == 0; }
#endif
