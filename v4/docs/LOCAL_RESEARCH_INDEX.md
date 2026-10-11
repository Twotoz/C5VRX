# IQ demodulation research archive

This extends **C5VRX by Twotoz and the C5VRX contributors**. Source:
https://github.com/Twotoz/C5VRX; official website and Discord invite:
https://twotoz.github.io/C5VRX/. Preserve GPL-3.0-only and source notices.
The inherited simulator, demodulators and historical pipeline research are
project work, not independent inventions by this contribution.

Base: PR190, commit `7491a660e26722afbb9dd9e24e69ba381b4f130d`.
The new research PR is stacked on that branch to isolate these artifacts.
The operator authorized publication on 2026-10-11. No runtime firmware,
selected model, default, RF setting or board flash is changed here.

## What survives the investigation

Read [the demodulation ledger](LOCAL_DEMOD_RESEARCH.md) for rejected formula,
counter, circular, velocity and instruction-path candidates. Rejected source
schedules and the invalid earlier counter run are retained with their labels.
Read [the IQ clock design](LOCAL_IQ_CLOCK_DESIGN.md) for the promising timing
observer, integer implementation, CPU estimate and unresolved output design.
The [carrier acquisition follow-up](LOCAL_IQ_CARRIER_DESIGN.md) records causal
plateau-frequency estimation, a false-noise admission, a stronger independent
gate and an oracle bound on how much correct centring could help U85Q sync.
The [controller and model follow-up](LOCAL_IQ_CONTROLLER_FOLLOWUP.md) records
automatic correction that restores target sync but harms nominal controls,
mixed imaginary-covariance estimates and six legal confidence-bank candidates
without a quality winner.

The strongest result is specific: received-IQ-only H-clock acquisition succeeds
in 16 fresh off-centre echo/fade cases at 6dB CNR, while U85Q's goggle H-lock
fractions average 0.953%. Another 16 fresh cases support a slower, cheaper
4ms acquisition variant. This does **not** demonstrate repaired video, colour,
physical C5 operation or improved flight range. Timing removes a declared
500ns simulator FIR delay; real receive-to-DAC latency is unknown. Broad
low-CNR failures and false timing are preserved, not excluded from the archive.

| Archive under `data/local_joint/` | Evidence |
| --- | --- |
| `screen`, `confirmation` | Formula screening and independent full-field rejection |
| `counter-confirmation`, `counter-corrected` | Invalid host assumption and corrected negative run |
| `circular-confirmation`, `velocity-confirmation` | Additional state/frequency strategies that regress |
| `pc-source-proof`, `pc-confirmation` | Official assembler acceptance, byte-exact control, rejected predictor |
| `complex-source-proof` | Official assembler rejection of overlapping counter/LUT routing |
| `sync-word-proof.json`, `descriptor-geometry.json` | Conditional output/transport constructions, without hardware acceptance |
| `iq-clock-reference`, `iq-clock-confirmation` | Detector development failures; threshold selection precedes validation |
| `iq-clock-validation` | Independent broad cases, including low-CNR and timing failures |
| `iq-clock-stress-validation`, `iq-clock-integer-proof` | Fresh 2ms stress acquisition and implementation equivalence |
| `iq-clock-half-rate`, `iq-clock-half-rate-integer-proof` | Fresh 4ms acquisition, equivalence and static instruction budget |
| `iq-carrier-development`, `iq-carrier-causal-validation` | Retrospective development, fresh-window estimates and rejected false-noise admission |
| `iq-carrier-coherence-validation` | Independent stronger gate; admitted wrong PAL timing still prevents controller promotion |
| `iq-carrier-pulse-validation`, `iq-carrier-pulse-replay` | Independent full-pulse guard and hash-matched rejection of the known wrong PAL clock; timing limitations remain |
| `iq-recenter-oracle-bound` | Known-direction settled correction restores much goggle sync, without better active-video fidelity |
| `iq-acquisition-loop-development`, `iq-acquisition-loop-retry-validation` | Received-only hypothetical controller; fresh retries restore target sync but regress nominal controls |
| `iq-carrier-imaginary-ablation`, `iq-carrier-filter-mismatch` | Conditional imaginary-covariance algebra and mixed estimator results, including fresh filter-mismatch cases |
| `pc-confidence-source-proof`, `pc-confidence-confirmation` | Six accepted source schedules; changed confidence radii fail independent quality comparison |

Frozen sources, recorded seeds, input hashes and compiler/assembler hashes
identify the experiments. Some early formula runs recorded source hashes
without snapshots; the ledger discloses this reproducibility limitation.
Snapshot files are historical executed versions, not automatically identical
to the current tool scripts. The host executable is regenerated, not committed.
Scoped Git attributes preserve source bytes so line-ending conversion cannot
silently invalidate the recorded hashes. `local_research_audit.py` checks JSON
and available snapshot/binary hashes; missing early snapshots remain a limitation.

## Reproduce locally

Use Python with NumPy, SciPy and Numba. The original environment was Python
3.13.15, NumPy 2.5.2, SciPy 1.18.1 and Numba 0.68.0. From the repository root
in PowerShell, set the import path and limit BLAS threads:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'v4/tools'
$env:OPENBLAS_NUM_THREADS = '1'
python v4/tools/dsp_search/local_iq_clock.py --seed 7500190 --outname replay-broad
python v4/tools/dsp_search/local_iq_clock.py --seed 7600190 --stress-repeats 8 --outname replay-stress
python v4/tools/dsp_search/local_iq_clock.py --seed 8000190 --stress-repeats 8 --stride 127 --train 160000 --outname replay-half-rate
python v4/tools/dsp_search/local_iq_clock_integer.py --frozen iq-clock-stress-validation --out replay-integer
python v4/tools/dsp_search/local_iq_clock_integer.py --frozen iq-clock-half-rate --out replay-half-rate-integer
```

Use separate replay directories to preserve the published evidence. The
integer replay reconstructs and checks recorded IQ hashes before comparison.
For standalone C semantics, Docker `gcc:14` is required; the runner tests
random and maximum-product data at seven chunk sizes. It expects a previously
generated `stream-rv32imc.s` in its output directory. The cross-compile below
uses the official `espressif/idf:v6.0.2` image and default entrypoint:

```powershell
$researchMount = (Get-Location).Path + ':/research'
New-Item -ItemType Directory -Force v4/docs/data/local_joint/replay-c | Out-Null
docker run --rm -v $researchMount -w /research espressif/idf:v6.0.2 riscv32-esp-elf-gcc -O3 -march=rv32imc -mabi=ilp32 -Wall -Wextra -Werror -DIQ_CLOCK_STRIDE=127 -S v4/tools/dsp_search/local_iq_clock_stream.c -o v4/docs/data/local_joint/replay-c/stream-rv32imc.s
python v4/tools/dsp_search/local_iq_clock_c_proof.py --stride 127 --out replay-c
```

For other candidate studies and source proofs, inspect each tool's `--help`.
BitScrambler proofs use the official ESP-IDF v6.0.2 assembler and ESP32-C5
target definition; their source URLs and hashes are in the proof JSON.
No assembly acceptance or host test substitutes for C5 bus/timing measurements.

## Next investigation: use the clock to aid carrier acquisition

An H clock can provide useful received-only sync/porch frequency statistics in
the simulated stress class. The follow-up demonstrates that narrower result
and an oracle sync benefit, but wrong admitted clocks still prevent a reliable
automatic controller. Full-pulse and separate porch consistency reject a known
bad clock in replay and pass another independent stress set; a remaining
timing error prevents treating carrier admission as permission for CVBS repair.

The current `rf_set_frequency_offset_khz` in `v4/main/rf.c` clamps to +/-1.5MHz
and suspends/restores the live path around the PHY write. `video_control.c`
requires at least 100ms after the last PHY write for an AFC observation.
Therefore instantaneous continuous retuning is an invalid simulation model.
The observer must also distinguish FM video deviation from carrier offset,
handle correlated noise bias and quantify wrong corrections on interference.
Retuning cannot recover information already removed by the analog filter.

Other unresolved gates are camera clock drift, V/field timing, received-only
level calibration, active-video fidelity, burst/colour, safe DMA ownership,
physical cycle budgets and flight comparison. The active research goal remains
open. Neither failed candidates nor this positive narrow result establish an
optimal or revolutionary demodulator.
