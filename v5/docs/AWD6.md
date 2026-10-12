# AWD6: adaptive-wrap phase-difference demodulator

Research and an opt-in firmware option for **C5VRX by Twotoz and the C5VRX
contributors**. AWD6 extends:

- the U85Q shared-LUT tracker and its DAC window (PR #190, `docs/FINDINGS.md`);
- the counter-A phase subtraction proven on the board by
  `main/bs_addctia_probe.bsasm` (C5VRX-3, `tools/bs_addctia_probe.md`) and the
  counter-assisted Phase8 endpoint delta of C5VRX-3/4;
- the floating predictive phase/frequency references of PR #198
  (`DEMOD_SEARCH.md`) and the earlier Alpha/PLL investigations (#53–55).

**Status: host emulator, official assembler and synthetic receive-chain
evidence only.** Board timing, picture, colour and range are not measured.
U85Q stays the default; AWD6 is chosen only for the WIDE profile and only
after console `W`.

## Why

The #198 host reference showed that a predictive tracker gains about 3 dB of
weak-signal luma SINAD over U85Q, but nobody had fitted it into the C5's two
bundles per 50 ns. A precision study of that reference (this work) found:

- 6-bit phase and 6-bit frequency keep almost all of the gain;
- the useful part is the frequency term, not phase smoothing. With the loop
  gain at 1 the tracker is exactly a **50-ns endpoint phase step whose
  +/-half-turn ambiguity is resolved around a slowly tracked centre** instead
  of a fixed one. A click then needs the noise phasor to travel about half a
  turn from the signal, not from a fixed point. U85Q and fixed-centre
  discriminators lose that margin on wide or off-centre VTXs;
- that centre may be coarse, so it fits in four LUT address bits.

## The program (`main/awd6.bsasm`, `tools/gen_awd_demod.py`)

Per 50 ns pair, using sample A of the raw pair like U85Q:

```text
theta  = 6-bit phase of the folded Q4/I4 cell                 LUT bank 0
D      = theta_k - theta_(k-1) mod 64     (312.5 kHz per step)  counter A high byte
e      = wrap(D/64 - c)                   c: one of 6 levels, -0.15..0.18 turn
output = DAC(c + e) over the U85Q window -6..+9 MHz           LUT banks 1..3
c'     = nearest level to c + 0.15 e, or one level toward      same LUT word
         the middle when e is only possible on the wrong branch
```

| Item | Value |
| --- | --- |
| Source | MODEM_DIAG ultrafine Q4/I4, PARLIO RX40, raw 32 KiB ring (unchanged) |
| Engine | TX BitScrambler only, 8 bundles (two per pair), no `jmp` |
| Per pair | read16, write16 `[D,D]` (unique CVBS20 at TX40), two LUT16 lookups, one `ADDCTIAH` + one `LDCTIAH` |
| LUT | 1024 x 16: 256 theta words, 768 AWD words (D x span parity x level) |
| State | counter A high byte; 3-bit centre level in the unconnected DAC bits 7, 14, 15 |
| CPU | none in the sample path; no ring-format change, no RX BitScrambler |

Hardware constraints that shaped it (from ESP-IDF v6.0.2 `tools/bsasm.py`):

- in LUT16 mode the LUT output overlaps counter B's source bits, so counter B
  cannot be read in a bundle that reads L. Both bundles read L, so only
  counter A is used;
- the LUT address is O16..O25 and the `ADDCTIAH`/`LDCTIAH` operand is
  O24..O31, so address bits 8..9 are also counter bits 8..9. The ADD writes 00
  there (no carry into the phase bits 10..15); the LOAD writes the centre's two
  high bits, which are never 00, so AWD words never alias the theta bank;
- there is no room to keep both +theta and -theta between bundles. The stored
  value therefore alternates sign with span parity (`w = +theta` on even
  spans, `-theta` on odd), so `D = w_prev + w` is +delta or -delta. The parity
  is an LUT address bit written as a constant by each program slot, so it can
  never drift; the centre level recovers within one span from any upset
  because theta words carry a valid default level.

### The centre range and the escape rule

The design went through three versions; both rejected ones are kept with
their holdouts.

1. **12 symmetric levels, +/-0.2 turn** (`data/awd6sym_holdout_20261012_superseded/`).
   Good host holdout, but a synthetic test found a **wrong-branch lock**: after
   a long sync-level stretch (the vertical interval) the centre sits low, and a
   white field on a deviation-2.0 VTX with +1.5 MHz AFC error is more than half
   a turn away. It wraps and is rendered **black** until the content changes.
   The random-texture rasters never held an extreme long enough to show it.
2. **6 levels confined to 0.01..0.18 turn**, where no content can reach the
   wrong branch (`data/awd6band_holdout_20261012_superseded/`). Large luma gains,
   but at 3-4 dB C/N with DC, a CW spur or a short echo it lost 20-37 % of the
   displayed lines in some cases. The centre could no longer follow a sync tip,
   so noise near the tip wrapped.
3. **Final: 6 levels from -0.15 to 0.18 turn plus an escape rule.** Below the
   middle (0.08 turn), an error below -0.35 turn moves the centre one level *up*
   instead of down, and the mirror rule applies above the middle. On the right
   branch that error cannot occur: from a low centre, white lies above it. So
   the rule only fires on a wrong-branch lock or on noise, and it walks the
   centre back within a few spans. On new design seeds (3-4 dB C/N, DC/spur/
   echo/IQ, deviation 1.7/2.0, 80 cases) the displayed-line share rose from
   0.45 (U85Q) and 0.59 (version 2) to 0.88, with no case more than 3 pp below
   U85Q. The setting sits on a broad plateau: every lower limit from -0.12 to
   -0.15, b from 0.12 to 0.25 and escape 0.35..0.40 gave 0.86-0.89.

`test_awd_demod.py` drives long sync -> white -> sync -> porch -> white -> sync
stretches through the emulated program for deviation 1.0..2.0 and AFC error
-1.5/0/+1.5 MHz. It also checks that a steady carrier from -6.4 to +10 MHz is
reproduced from every start level, and the escape transitions.

## Verification

- `tools/bs_emu.py` emulates the bsasm subset bundle by bundle. It reproduces
  U85Q, U85Q kp0.6/kp0.5 and U85G bit-exactly against `sim.py`, and the C5VRX-3
  counter probe exactly as its board run reported.
- The emulated `awd6.bsasm` equals the plain reference model
  (`gen_awd_demod.model`) sample for sample after startup, on random bytes and on
  a simulated FM case.
- The official ESP-IDF v6.0.2 assembler with the ESP32-C5 capability file
  accepts the program: 8 instructions, 1024 LUT words.
- The emulator is not a timing model. `ADDCTIAH`/`LDCTIAH` semantics follow the
  ESP-IDF documentation and the C5VRX-4 Phase8 span75 programs that ran live. A
  bit-exact board check is still open (see below).

## Host results

Frozen design, checked-in `main/awd6.bsasm` run through `bs_emu.py`, against
the U85Q variant of the same firmware profile. Seeds never used in design
(`data/awd6_holdout_20261012c/`, 474 cases, `research/awd_study.py`).

| Split | Weak mean luma SINAD | Strong mean | Worst SINAD | Worst displayed lines | Mean displayed lines U85Q -> AWD6 |
| --- | ---: | ---: | ---: | ---: | --- |
| #198 validation matrix (WIDE, 56) | +2.63 dB | +1.06 dB | -1.17 dB | -1.9 pp | 0.871 -> 0.920 |
| Situation matrix, WIDE (dev 1.7/2.0) | +3.35 dB | +2.87 dB | -0.87 dB | -2.5 pp | 0.864 -> 0.953 |
| Situation matrix, DEFAULT (dev 1.4) | +0.81 dB | +0.33 dB | -0.95 dB | -4.8 pp | 0.973 -> 0.992 |
| White/black content, WIDE | +6.70 dB | +9.38 dB | -1.76 dB | 0 | 0.495 -> 1.000 |
| Situation matrix, KP060 / KP050 | -1.03 / -2.55 dB | | -2.5 / -3.9 dB | | (not used) |

On WIDE no case loses more than 3 pp of displayed lines or drops below 90 %
lock where U85Q holds it. The remaining per-case gate failures are luma
SINAD only (6 of about 190 WIDE cases, worst -1.17 dB): a -1.5 MHz AFC
residual, where U85Q happens to sit at its best and an ideal floating-point
endpoint discriminator shows the same dip, deep fades at 8 dB C/N, where
AWD6 output in pure noise is about 1 dB noisier, and one echo case. U85Q
loses half of the picture on long white/black fields of a wide VTX with
+/-1.5 MHz AFC error (its 32-phase tracker slips); AWD6 keeps all of it. The narrow
profiles need U85Q's phase smoothing (kp 0.5/0.6), so AWD6 is WIDE-only.

Two rejected versions were held out on their own seeds (see above). The
symmetric version scored similarly on random rasters, but it failed the
content test; the confined band lost lines at 3-4 dB C/N. Synthetic WIDE/16 MHz
model, simplified ADC, propagation and goggles: luma and goggle-model lock,
**not RF sensitivity or distance**.

## Enabling it

Console `W` stores NVS `c5vrx5/demod` (0 U85Q, 1 AWD6) and reboots. With AWD6
selected, a VTX measured at startup as WIDE (deviation >= 1.6) runs AWD6. Every
other profile keeps its U85Q variant. `T` reports `demod=AWD6_EXPERIMENTAL` when
it is active. Press `W` again to return to U85Q.

## Board tests still required

1. Loopback the program through `bitscrambler_loopback_run` with recorded IQ
   (or `capture_iq_snapshot.py` data) and compare it with `bs_emu.py` byte for
   byte, including startup and ring wraps.
2. Live: TX FIFO/overload counters (`T`), a continuous 30-minute soak, then
   PAL/NTSC picture, colour and sync on the goggles at a matched gain/filter.
3. A/B/A U85Q versus AWD6 on the same wide VTX, with conducted attenuation steps,
   then the same outdoor route and antenna geometry. Record luma, sync and
   distance separately. No range improvement is claimed before that.

Reproduce:

```sh
python -m pytest v5/test/test_awd_demod.py
python v5/tools/gen_awd_demod.py --check
python v5/research/awd_study.py v5/research/data/<new directory>
```
