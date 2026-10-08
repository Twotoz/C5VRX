# PAIR range study: using both IQ40 samples in the shared-word tracker

This extends C5VRX by Twotoz and the C5VRX contributors, in particular the
RANGE32 shared-word tracker, the safe-range protocol and its negative results:
https://github.com/Twotoz/C5VRX. Website and Discord: https://twotoz.github.io/C5VRX/.
It follows the ledger's issue #23 direction that every acquired 40-MS/s IQ
sample should participate before 2:1 reduction. Nothing here is measured RF
range, goggle acceptance or a global optimum.

## Why the second sample matters

Every plain shared-word tracker (OVP-style decoders, RANGE32, RANGE32+, MAX)
addresses the decoder with the first raw byte of each 50-ns span. The second
acquired sample is read by the BitScrambler but ignored (context mode used two
sign bits with a bounded heuristic weight). A host floating-point upper bound
on the fine-lane synthetic model (PAL/NTSC, C/N 2..8 dB weak, 30 dB strong):

| Floating-point phase loop | weak luma SINAD | missed H/V | strong SINAD / detail |
| --- | ---: | ---: | ---: |
| RANGE32 executable LUT | 16.1 dB | 42 | 15.8 / 0.73 |
| first sample only, 20 MHz | 19.1 dB | 2 | 17.2 / 0.80 |
| both samples, fixed carrier-prior rotation | 22.2 dB | 2 | 18.4 / 0.84 |

The ten-bit decoder address cannot carry both full samples. Truncating the
first sample (3322, 3232, 4222, ...) lost strong-picture quality in every test;
**PAIR4411** keeps the full first byte and adds the sign bits of both
components of the second sample. Geometric mid-cell combination of those bits
was worse than RANGE32. The working decoder is learned instead: for each of
the 1024 addresses, the mean unit phasor of the clean received carrier at the
first sample (`pair_decoder.py`). Its angle is the observation and its length
the reliability. Training uses separate seeded signals over C/N, amplitude,
offset and echo mixtures; the firmware only receives the LUT.

## Hardware schedule

The controller writes ten chosen input bits into address bits16..25 instead of
`raw byte + H,H`; decoder tokens occupy the high bits of all 1024 words, as in
the existing context schedule. Eight slots, two bundles and two lookups per
50 ns, LUT16/2 KiB, raw Q4/I4 RX40, raw32K ring, TX-only BitScrambler and
duplicated DAC6 at physical 40 MHz are unchanged. No CPU sample loop,
transformed ring, DMA-boundary reset or live LUT writer is introduced.
`test_pair.py` checks five layouts against the source-driven BitScrambler
model over 70k-sample streams; `test_range_options.py` adds an independent
standard-library PAIR address implementation. Finalists assemble with the
ESP-IDF ESP32-C5 BitScrambler assembler (`prove_range_finalists.py`).

## Search method

`search_pair_range.py` evolves complete compiled trackers (PAIR4411 with six
learned decoders, and plain first-sample trackers under the same budget):
parallel islands, self-adapting Gaussian steps in normalized parameter space,
crossover, neighbouring-topology moves and stagnation restarts. Every scored
model is a deduplicated LUT/schedule. Workers checkpoint leaders and hashes so
interruptions keep evidence. `robust_select.py` re-scores every checkpointed
leader on six fresh screens and ranks by number of screens passing every
strong/burst guard, then mean objective. `refine_range.py` (unchanged) fits
the DAC; `validate_range.py` (unchanged gates) performs independent
confirmation against matched RANGE32.

## Results

Two completed one-million searches (per-worker unique compiled LUT/schedules;
union 999,992 and 999,985) were followed by robust selection, the unchanged
refinement and the unchanged `validate_range.py` independent confirmation
(fresh seeds, full PAL/NTSC fields, echo/fade, amplitude/offset envelope,
carrier outage and unseen zone-plate/checker/texture content).

**Run C (seed340000, envelope screens) confirms PAIR `RNG-e2a8f30af45e`**
(PAIR4411, 32 phases, 32 tokens, learned observations, fitted DAC):

| Independent metric | RANGE32 | PAIR e2a8 |
| --- | ---: | ---: |
| Nominal weak H+V misses, C/N0..10 | 3,715 | 2,558 |
| Mean weak luma SINAD | 15.875 dB | 18.018 dB |
| First passing tested nominal C/N | 10 dB | 10 dB |
| Strong waveform SINAD / detail | 15.940 / 0.721 | 16.355 / 0.742 |
| Echo/fade weak misses | 3,006 | 2,562 |
| Echo/fade first passing tested C/N | 30 dB | 22 dB |
| Unseen content weak misses (C/N6/10) | 40 | 7 |

All strong, envelope, outage and content gates pass. The nominal usable C/N
grid step is 2 dB; equal thresholds do not exclude a smaller shift and the
table is not a measured RF sensitivity. Its source assembles with the ESP32-C5
BitScrambler assembler and its 20k-sample stream is bit-exact against an
independent standard-library PAIR implementation.

**Run A (seed320000, nominal screens) confirms plain `RNG-5cc14ad73003`**
(first sample only; misses 2,960 vs 3,673, weak luma 17.343 vs 15.897 dB,
strong 16.241/0.734 vs 15.968/0.721). Its PAIR leaders failed the 0.5 MHz
offset envelope (see below). On a matched fixed-ultrafine screen (RMS4.5, the
board's edge noise) 5cc14 also beats RANGE32 (weak luma 17.52 vs 15.66 dB).

Robust selection on run C found no candidate passing all six envelope screens;
the best PAIR leaders passed five. Full-field confirmation, with many more
bursts and lines, decided. Run D (fixed-ultrafine edge profile) is separate.

Shared floating-point controls in the same confirmation keep a higher weak
SINAD (22.4 dB) but miss more pulses (3,876), first pass at 12 dB and fail
strong guards; they are a reference, not an executable C5 target.

## Negative results retained

- **Interrupted 3M run** (seed310000): stopped after about245k evaluations
  because the best score was flat from minute three and leaders were only
  written at the end. Excluded; checkpointing was added.
- **Run A PAIR finalists** (seed320000) led every short screen but lost sync
  at a 0.5 MHz carrier offset in full-field selection (about600 missed lines)
  and failed detail/burst guards at rms1.5/5. Search screens had used only
  rms3/1 MHz. Run C adds six jittered amplitude/offset strong cases to every
  search and robust-selection screen. Validation gates were not changed.
- **Run B** (seed330000, old objective) was stopped at about125k evaluations
  in favour of run C; its checkpoints are not used.
- **State-conditioned decoder**: using the top two phase-state bits as decoder
  address bits (relative tokens, uniform or companded) gave no gain over
  RANGE32 (score1.10 vs0.99) or was worse. The bottleneck is input
  information, not token resolution.
- **Direct LUT-entry polish** (`polish_lut.py`, DAC/phase/token +-1 on visited
  entries, held-out checkpoints): no move survived held-out screens. Single
  entry gains are below screen noise; retained as a safeguard, not a method.
- **75-ns cascade decoders**: even an ideal floating loop using all three
  samples at full resolution (luma18.4dB, strong15.8dB) is worse than the
  50-ns first-sample loop (19.1/17.2dB). Up to about125 degrees of FM phase
  rotation per 75 ns leaves too little margin before cycle slips.
- **25-ns single-lookup trackers** (one bundle and one lookup per sample,
  address = phase state + sample bits, unique DAC40): best of 288 exact LUT
  variants per layout improves weak luma (17.06 vs 15.68 dB for RANGE32) but
  fails strong guards (SINAD 13.63 vs 15.95 dB, detail 0.663 vs 0.728) for
  16 phases with I3/Q3; 32 phases with I3/Q2 or I2/Q3 is similar. Ten address
  bits cannot hold both a fine phase state and a fine sample.
- **Short-screen burst guards** are noisy: one-screen colour-burst jitter
  failures occur for good models. Robust selection counts passes over six
  screens instead of relaxing any guard.

## Reproduction

From `v4/tools/dsp_search` with the pinned research environment and
`OPENBLAS_NUM_THREADS=1`:

```
python search_pair_range.py --output /fresh/c --seed-base 340000 --evaluations 1000000 --workers 7
python robust_select.py --search /fresh/c --output /fresh/c-robust
python refine_range.py --search /fresh/c-robust --output /fresh/c-refined
python validate_range.py --search /fresh/c-refined --output /fresh/c-confirmed
python ../prove_range_finalists.py /fresh/c-refined/frozen.json --idf $IDF_PATH --output /fresh/c-proof
python test_pair.py
```

## zerowidth/C5VRX PR #3 review (2026-10-08)

Credit: zerowidth/C5VRX PR #3, `docs/p4-receiver-findings.md` (head
5652b347), C5 + P4 receiver measurements. Each finding was checked against
this standalone RX40 path rather than assumed to transfer.

- **Interference bursts (built).** Wi-Fi bursts of 0.1-0.5 ms clipping every
  sample held his weak carrier 20+ steps low. In the V5 host model a weak
  edge carrier at G83 fell to G20 (10 writes, 49 overloads in 240 ms).
  V5 now counts saturation only after 1 ms (`DG3_BURST_US`); the burst
  regression keeps G83 with zero writes and persistent overload still drops.
  The operator's board (old firmware, no VTX, 72 min) logged 4,530 V5 writes,
  consistent with but not proof of burst-driven drops; `bursts_ignored` now
  reports it.
- **Gain table limit 77 (not applicable).** His decode used the 2.4 GHz spans
  that PR #174 corrected here. The board reports table_max 83 and 0 clip_pm at
  G83 without a VTX.
- **DC offset at high gain (measured, no change).** Board, fixed ultrafine,
  G83: I -0.23, Q +0.04 cells with per-gain hardware DCO active, small
  against the edge noise radius (~1.7 cells).
- **Fourth-difference sampling test (not ported).** On the host lane model with
  the board's ~19.4 MHz filter, clean and mixed 4-bit reads scored alike
  (noise 36.7 vs 35.0; carrier 11.3 vs 10.7): his criterion relies on an
  almost empty band above ~10 MHz (802.11p filter) and 7-bit lanes. The
  existing check reports settled/0 ppm. Porting needs real IQ, currently
  blocked because guarded snapshots refuse copies of 65-234 us (>50 us).
- **802.11p, despeck, sync slicer, click handling:** the fixed calibrated
  filter and edge gear already cover the filter result; the others belong to
  his software decoder, not the analog goggle path.
