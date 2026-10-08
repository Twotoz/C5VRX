# Shared-word tracker architecture and range search

[RANGE32 results](RANGE32_RESULTS.md) records the confirmed synthetic winner,
negative searches, teacher/output fitting, real counter/context prototypes,
matched-work strategy comparison and outstanding physical acceptance.

This extends C5VRX by Twotoz and the C5VRX contributors and its existing
phase/trajectory/PLL studies: https://github.com/Twotoz/C5VRX.
The official website and Discord invite are https://twotoz.github.io/C5VRX/.
Existing licenses and donor credits remain. Candidate counts do not establish
a global optimum, a new invention, physical sensitivity or goggle acceptance.

## Hardware architecture

The old PLL96 divides LUT16 into 768 transition entries and 256 decoder
entries. The new compiler shares the words: decoder tokens occupy the high
bits of entries768..1023, independently of the DAC and state fields in their
low bits. All 1024 words can now carry transitions.

For token width b in 2..6, state width is s=10-b. Each word contains
`DAC6 | next_state<<6 | optional_decoder_token<<(16-b)`. The decoder lookup
uses only the high b bits; the transition lookup uses only the low 6+s bits.
Their widths sum to16, without overlap. Lookup addresses are
`768+raw_byte` and `(state<<b)+token`.

This gives observation/state allocations4/256,8/128,16/64,32/32,64/16.
The state can represent phase and frequency, or phase alone. Two lookups,
eight instructions, two bundles/50ns, LUT16/2048 bytes, IQ40/raw32K/TX-only,
unique DAC20 with [D,D] at physical DAC40 are retained. There is no CPU sample
loop, transformed ring, DMA-boundary reset, live LUT adaptation or concurrent
RX/TX BitScrambler. The hardware cost gate rejects unimplemented schedules.
Five allocations assemble with the ESP-IDF6.0.2 C5 target and match independent
instruction execution over126,976 state/raw combinations plus five70k streams.
This establishes source addressing/dataflow, not measured board timing.

## Mathematical search

Let `phi` and `omega` be the tracked phase and frequency at unique20MHz:

```
prediction = phi + omega
error      = wrap(observed_phase - prediction)
innovation = error | clip(error) | sin(error) | limit*tanh(error/limit)
gain       = bounded(alpha * confidence * optional_error_adaptation)
omega_next = Q_frequency(omega + beta * confidence * innovation)
phi_next   = Q_phase(prediction + gain * innovation)
```

Observation phase can use uniform angular bins or raw-cell complex centroids;
optional amplitude groups encode confidence in the same token budget. Generated
output equations include an independent innovation estimate, continuous phase
advance, quantized state-phase advance, and a mixture of the latter two.
The advance variants tie reconstructed frequency to the phase evolution rather
than treating reconstruction as an unrelated output gain. Saturation and DAC6
rounding happen offline. The firmware executes only the generated table.
With one frequency state this is a first-order phase tracker, not a second-order
frequency-integrating PLL. A carrier-centre parameter is a predictor, not an
RF retune or an AFC write.

Finer absolute frequency-state grids are not the only way to improve output.
At20MHz a32-phase grid advances in625kHz-equivalent steps, but the continuous
advance/mixed output need not equal that grid. With the preserved Phase50
transfer, DAC6 itself has about146kHz instantaneous equivalent spacing over
its9.2MHz full span.100/50/25kHz targets therefore also require considering
temporal averages/residue coding and DAC quantization; adding frequency states
alone cannot guarantee those instantaneous output resolutions. None of these
numbers is a measured RF tuning resolution or a proven minimum for video.

Each run evolves actual compiled LUTs using bounded mutations of successful
parents plus fresh architectures. Equivalent LUT bytes/token layouts are
deduplicated. Different parameters can produce identical quantized behaviour;
they are not counted twice within a run. The search explores this finite
family, not all mathematically possible C5 demodulators.

## Validation protocol

All detectors receive identical signed I-high/Q-low IQ bytes. One positive
clean OVP56 calibration is shared; no candidate-specific gain, polarity or
delay fitting can conceal a bad reconstruction. Complete interlaced PAL/NTSC
fields include equalizing/broad vertical pulses, H-sync, blanking, burst,
black/white patches and fine luma/chroma. OVP56, VLP56, HC50, rejected PLL96,
IQ-only repaired PLL96, the currently flashed repaired PLL96 and floating PLL
are controls. Synthetic C/N is not RF input dBm.

The usable-picture screen requires positive polarity, contrast65..120%,
waveform SINAD>=8dB, <=1% missed H pulses, <=2 consecutive missed H pulses,
no missing vertical pulses, two vertical trains, H/V timing95th percentile
<=0.35us and H-width RMSE<=0.25us. These are declared engineering proxies;
they do not establish that particular goggles lock at that C/N.

Healthy strong-signal gates require SINAD>=14dB and within1dB of OVP56,
contrast85..115%, no missing sync, bounded timing/width and sync/black/white
level errors. Fine-detail high-pass correlation may fall by at most0.03.
Allowing1dB aggregate waveform loss is a deliberate experimental trade-off for
the operator's range-with-usable-picture objective, not proof of identical
strong-picture quality. Real side-by-side acceptance remains necessary.
Under low IQ occupancy or echo/fading, where OVP56 itself can fail the
absolute healthy-source gates, reference-relative checks reject additional
misses, >0.25dB strong waveform loss and excessive level/timing degradation.

The robust round adds IQ RMS1.5/3/5 and carrier offsets0.5/1/1.5MHz to its
training screen. Full-field selection freezes one model before two independent
nominal seeds, a separate echo/fade seed and four amplitude/offset holdouts.
A final veto cannot be bypassed by selecting a runner-up from the same data.
Weak aggregate SINAD must stay within0.1dB of OVP56, with no additional missed
pulses and no worse usable threshold; an actual improvement is also required.

The overlay timing metric includes missed pulses, capped at2.025us, in its
95th percentile. The older diagnostic reported only matched-pulse jitter;
that can make a failing reference appear better by omitting its missing pulses.
Both values are retained in CSV. Vertical-train regression compares distance
from the truth's train count, so recovering a train is not incorrectly rejected
for differing from a broken reference. Regression tests cover both cases.
Historical earlier-run evidence retains its original metrics and decisions.

## Reproduction

Install the pinned `tools/dsp_search/requirements.txt` in a research virtual
environment. The ordinary firmware verifier does not require NumPy/Numba.
Set `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`.

```
python tools/dsp_search/search_overlay.py --output /new/search --evaluations 500000 --seed-base 15000 --detail-gate --robust-envelope
python tools/dsp_search/validate_overlay.py --search /new/search --output /new/confirmation
python tools/dsp_search/leaderboard.py /new/search/leaderboard.sqlite --limit 20
python tools/dsp_search/leaderboard.py /new/search/leaderboard.sqlite --pareto
python tools/dsp_search/test_overlay.py
python tools/dsp_search/test_video_metrics.py
```

Output directories must be fresh. SQLite retains every candidate, parameters,
status and independent proxy metrics. The Pareto frontier is a screening
trade-off view, not independent validation. Frozen finalist models and per-frame
CSV preserve decisions. Large local databases are experiment artifacts rather
than firmware assets.

## Real IQ evidence

Serial uppercase `I` exports one guarded8190-byte completed-ring snapshot,
with source format/rate/gain/lane/frequency and a FNV1a checksum. Capture with
`python tools/capture_iq_snapshot.py COM_PORT new.raw`. It refuses stale,
settling or busy contexts and does not reset/retune the PHY. CPU work is bounded
observational copying, not live demodulation. Export yields between hex chunks.
An8190-byte snapshot covers204.75us (about three lines), cannot establish
vertical lock or range, and has unknown initial DSP state. Host replay remains
diagnostic without a known transmitted waveform. No new actual IQ capture was
available for this search; synthetic and physical results remain distinct.
