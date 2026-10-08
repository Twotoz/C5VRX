# Fresh range search after MAX board failure

C5VRX by Twotoz and contributors: https://github.com/Twotoz/C5VRX.
Website/Discord: https://twotoz.github.io/C5VRX/.

The previous4million run found much better weak pulse counts, but its MAX
candidate was noisy/desynchronized on the board. The operator confirms that
restoring RANGE32 restores good video. MAX is a negative regression fixture,
not a successful range improvement. The board stays on RANGE32. C5VRX-5 is
deferred until the operator explicitly requests it.

Previous search/fitting used ordinary Q4 quantization and final confirmation
used the actual fine{9,7,6,5} lane model. This fresh study uses fine lanes in
search, teacher/DAC fitting, selection and independent confirmation. A bit
model still does not establish actual analog ADC noise/calibration. Existing
IQ export refused `stale_or_settling`; no valid board capture was obtained.
The exporter now reports source/copy/epoch/deadline context without relaxing
any freshness or DMA exclusion. Its diagnostic update is not yet flashed.
Code investigation also found a deterministic false-refusal bug: descriptor
discovery starts at the active DMA node, whereas snapshot validation required
the first node's buffer to be ring byte0. Validation now accepts any rotation
of the contiguous physical ring, still requiring complete non-overlapping
coverage, unchanged active descriptor, <=50us copy time, no possible DMA lap
and unchanged gain/PHY/lane epochs. The host regression checks every rotation
including the short tail node and rejects gaps, overlaps and invalid lengths.
This code-level fix is not yet a successful board capture or a diagnosis of
which refusal condition occurred in the old firmware.

Four fresh policies each completed1million unique LUT/schedule evaluations:
plain(base70000), bounded next-IQ context(base80000), radius confidence
(base90000), compact frequency memory(base100000). All require>=16 angular
observation tokens and>=16 phase states. This is a risk-reducing search scope,
not a proven hardware minimum or diagnosis that coarse states caused every
physical failure. All retain actual2KiB/eight-slot/two-bundle50ns schedules,
raw32K IQ40, TX-only execution and duplicated DAC6 output at40MHz.

The `safe_range` profile predeclares strong SINAD>=14dB, contrast85..115%,
detail correlation>=.65, level errors<=5IRE, H/V jitter<=.35us and H width
RMSE<=.2us. Against RANGE32, strong waveform loss is capped at1dB and detail
correlation loss at.03. Unhealthy stressed references use bounded relative
guards, with the same1dB/.03 limits. Burst gain must stay within70..130% of
the matched reference; phase jitter may increase by at most5degrees,
amplitude jitter by10percentage points and burst RMSE by3IRE. These are
engineering screens, not certified goggle-lock criteria.

Weak ranking retains low-band information and missing-pulse loss, and now
subtracts2 times mean bounded H width RMSE and2 times mean H+V jitter in us.
Missing pulses already incur their separate penalty; unmeasurable widths
are bounded at2.025us rather than treated as zero. The common usable screen,
independent final seeds, offset/fade/outage tests and frozen-winner/no-runner-up
rule remain unchanged. Selection data never becomes final confirmation data.
No method is promoted or flashed automatically after synthetic success.
If selection freezes the exact existing RANGE32 LUT/schedule, it cannot
strictly outperform itself on matched inputs. The validator now records that
identity rejection and skips duplicate final simulations. It does not replace
the winner with a runner-up or label selection rows as final confirmation.
Already started validators may finish the equivalent explicit comparison;
their original source hashes distinguish that path. Neither counts as a new
confirmed model.

Before any finalist freeze, at2026-10-08 08:53:54UTC, an additional content
holdout was registered while the searches were running. It uses zone plates,
moving checker transitions and seeded interpolated texture, retaining the
same sync/porch/burst/flat calibration patches. These patterns are never
used by the search or DAC fit. Full PAL/NTSC cases at C/N6/10/30dB use fresh
seeds+561..563. Strong picture/burst guards still apply. Weak total H/V misses
must not exceed matched RANGE32, and mean bounded H/V jitter and H width
RMSE may worsen by at most.1us. This revision precedes inspecting any new
frozen-winner outcome; the search launch hashes and historical studies stay
unchanged. Independent confirmation records the revised source hashes.

Confirmation also reports spurious sync-like pulses (>=1us below the fixed
sync threshold, farther than2us from every expected sync start), separately
from missing true pulses and normalized by expected H lines. This diagnostic
does not change the predeclared selection/gates. Neither pulse count proves
goggle lock; real sync separators also depend on pulse shape and filtering.
On the already failed MAX, a retrospective fine-lane diagnostic at seed40401
finds fewer spurious pulses than RANGE32 at C/N4/6, and none for either at30dB.
Thus this metric alone does not explain the physical failure. MAX still fails
the strong waveform, detail, level and colour-burst guards. The same synthetic
signal can have zero missing sync starts and still have unacceptable video.
The [retrospective rows](data/range_priority/max_false_sync_diagnostic/metrics.csv)
are diagnostic reuse of old data, not new confirmation. VLP56 is included
alongside all other controls in the fresh independent range confirmations.

## Completed search workload

All four search budgets completed. Uniqueness is compiled LUT/schedule-vector
hash uniqueness within each run, not a proof of distinct observable behaviour
or a cross-run/global optimum. There were5,967,202 proposals, of which
1,967,202 repeated within-run hashes were skipped. Of4,000,000 evaluated
vectors,2,118,002 passed tone screens;11,047 received detailed short-video
screens and3,425 passed those training guards. These are staged evaluations,
not4million full-field independent video confirmations.

| Policy | Unique LUT/schedules | Detailed short screens | Eligible short screens | Search wall seconds | Capped fits | Refined vectors | Frozen/assembled entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Plain phase tracking | 1,000,000 | 2,831 | 781 | 2,367 | 72 | 216 | 14 |
| Bounded next-IQ context | 1,000,000 | 2,948 | 963 | 2,376 | 54 | 165 | 12 |
| Radius confidence | 1,000,000 | 2,596 | 833 | 2,059 | 30 | 95 | 10 |
| Compact frequency memory | 1,000,000 | 2,672 | 848 | 2,393 | 30 | 95 | 9 |

All45 frozen entries assembled with the actual ESP-IDF6.0.2 ESP32-C5 target.
They include the pinned baseline in multiple runs;45 is not a globally unique
model count. The186 bounded fits produced571 per-refiner unique vectors,
including retained bases. The fits, source hashes, resource allocations,
selection rows and independent decisions are retained in the
[evidence directory](data/safe_range/README.md). Proxy convergence/Pareto
records use training data only and are not independent range evidence.

## Independent decision: retain RANGE32

No new option passes every frozen gate. Context, confidence and compact-memory
selection each returns the exact pinned RANGE32 LUT/schedule. Confidence
finishes its already running explicit final comparison; context/compact record
identity rejection and skip duplicate finals. No runner-up is substituted.

Plain tracking freezes `RNG-58c0e88fcba8`. It is a promising research near-miss,
not a confirmed replacement or a physically failed model. Its independent
nominal weak missing-pulse count falls21.95%, while strong nominal detail is
essentially preserved. The full decision still rejects it:

| Independent metric | RANGE32 | RNG-58c0e88fcba8 |
| --- | ---: | ---: |
| Nominal weak H+V misses, C/N0..10dB | 3,676 | 2,869 |
| Mean weak waveform SINAD, dB | 6.943 | 6.527 |
| Mean weak low-band SINAD, dB | 15.869 | 17.510 |
| First passing tested nominal usable C/N, dB | 10 | 12 |
| Nominal strong waveform SINAD, dB | 15.935 | 15.739 |
| Nominal strong detail correlation | .720485 | .721050 |
| Echo/fade weak H+V misses | 3,055 | 2,737 |
| Echo/fade strong detail correlation, PAL | .619866 | .587671 |
| Unseen content weak H+V misses, C/N6/10dB | 25 | 11 |

At PAL C/N10/seed70401, both recover every sync pulse, but the candidate's
95th-percentile V timing error is.50625us versus.0825us for RANGE32. The
predeclared usable bound is.5us: the breach is only.00625us, and the2dB grid
step is not a measured2dB RF penalty or proof of unusable hardware video.
It nevertheless fails this frozen screen. PAL strong echo detail loses.032195
against the allowed.03, a second small but real veto. Offset/occupancy,
post-outage recovery and unseen-content checks add no veto. The derived
[decision audit](data/safe_range/plain/confirmation/decision_audit.json)
identifies these exact rows; no thresholds were moved after seeing them.

The candidate is phase32/observation32 with no confidence partition, next-IQ
context, counter or separate frequency state. Its analytical centroid encoder,
clipped innovation and adaptive gain use `kp=.804178`, fixed prior1.243868MHz,
phase rotation.006100rad, limit.776771rad and adaptation1.465077. DAC output
mixes continuous and quantized phase advance with fraction.521975, followed
by the existing absolute DAC6 scale. It is an unfitted analytical base model;
the exact equations are in `overlay_fsm.synthesize`, and the frozen LUT and
[generated source](data/safe_range/plain/confirmation/experimental.bsasm)
fully define the executable model. Unused parameters in the serialized
single-frequency template do not imply additional hardware state.

Because no new option qualifies individually, the predeclared shared-input
comparison freezes zero options and is skipped. Its
[protocol and decision](data/safe_range/shared/decision.json) are retained.
No new firmware mode is enabled, no board flash occurs, OVP56 remains default
and the operator's working RANGE32 remains unchanged. This study supports
RANGE32 as the current working choice within the declared search, not global
optimality. Further promotion needs actual board/goggle evidence; the IQ
export geometry fix remains unflashed and no valid new live IQ is claimed.
C5VRX-5 remains deferred until the operator explicitly requests it.

## Reproduction

From `v4/`, with NumPy/SciPy/Numba and BLAS threads fixed to1:

```
python tools/dsp_search/search_safe_range.py --policy plain --seed-base 70000 --evaluations 1000000 --output /fresh/plain
python tools/dsp_search/search_safe_range.py --policy context --seed-base 80000 --evaluations 1000000 --output /fresh/context
python tools/dsp_search/search_safe_range.py --policy confidence --seed-base 90000 --evaluations 1000000 --output /fresh/confidence
python tools/dsp_search/search_safe_range.py --policy compact --seed-base 100000 --evaluations 1000000 --output /fresh/compact
python tools/dsp_search/refine_range.py --search /fresh/plain --output /fresh/plain-refined
python tools/dsp_search/validate_range.py --search /fresh/plain-refined --output /fresh/plain-confirmed
```

Repeat refinement/confirmation per policy. Record completed counts and
per-frame evidence after execution; the stated budgets are targets until
their summaries exist. Preserve the previous negative evidence separately.

For each refined frozen set, run the real ESP32-C5 assembler from an activated
ESP-IDF environment (no NumPy/SciPy/Numba needed for this step):

```
python tools/prove_range_finalists.py /fresh/plain-refined/frozen.json --idf /path/to/esp-idf --output /fresh/plain-proof
```

The proof records model, source, binary, assembler and target hashes alongside
the resource allocation. A failed assembly never produces a success proof.
Assembler acceptance still does not establish live timing or physical video.

At2026-10-08 09:24:45UTC, before any new independent confirmation outcome,
a second, shared-input comparison was registered. Only individually confirmed
options may enter; identities are checked and duplicate LUT/schedules removed.
Fresh shared selection uses seed200301; its frozen winner then faces nominal
seeds200401/200402, echo/fade200501, envelope200511..514, outage200531 and
content200561..563. It uses exactly the same safe_range gates and no-runner-up
rule. This separates selecting across policies from the final independent
decision. If none qualify individually, skip this comparison and retain RANGE32.

```
python tools/dsp_search/compare_safe_range.py --confirmed /fresh/plain-confirmed /fresh/context-confirmed /fresh/confidence-confirmed /fresh/compact-confirmed --output /fresh/shared
python tools/dsp_search/validate_range.py --search /fresh/shared --output /fresh/shared-confirmed
```
