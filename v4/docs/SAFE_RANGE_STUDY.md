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

Four fresh policies each target1million unique LUT/schedule evaluations:
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
