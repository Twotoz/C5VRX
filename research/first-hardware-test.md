# First ESP32-C5 hardware procedure

This is the exact evidence-gathering sequence for the first board. It does not
enable production `LIVE START` unless all gates are measured.

## Connections

1. Flash the RF-deep-probe/Receiver Console firmware built from the pinned
   ESP-IDF v6.0.2 profile.
2. Connect USB-C and the six-bit passive DAC to a high-impedance scope first;
   connect the HDZero 75-ohm AV input only after the CVBS test levels are safe.
3. Select a receiver center inside the C5 specified range. A4/5805 MHz is the
   simplest exact baseline.
4. Feed a coherent, attenuated tone at center +2 MHz (A4: 5807 MHz). Do not
   attach an unattenuated VTX or signal generator to the antenna input.

## One-button suite

Open `C5VRX Receiver Console`, connect, and press **FIRST HARDWARE TEST**. It
queues, in order:

```text
STATUS
WBFM HWTEST
PRODUCER CADENCE PROBE ALL
WRAP FLAG PROBE 0
PHASE CONTINUITY PROBE 0
PRODUCER SOAK 0 30000
BENCH SPARSE 2
BENCH SPARSE 4
BENCH SPARSE 8
BENCH BITSCRAMBLER
BENCH PARLIO
BENCH PIPELINE
BENCH USB PREVIEW
BENCH RING PIPELINE 0 1000
USB PREVIEW STOP
CAPABILITIES
STATUS
```

When an unambiguous mode-0 cadence arrives, the console automatically appends
`FINE TUNE VERIFY <center> <center+2> <measured_rate>` so tone offsets are
reported before the reconstructed producer, during it, and after teardown.

The 30-second soak command internally runs 1 ms, 10 ms, 100 ms, 1 s, 5 s and
30 s stages, stopping at the first invariant failure. No unlimited run exists
in the normal parser.

## Pass gates

- mode 0 has `classification=MEASURED`, no ambiguous cadence intervals, a
  changing pointer/RAM, and exact register restoration;
- bit 18 correlation is reported as observation, not silently named wrap;
- phase test uses the coherent tone, has high coherence/magnitude and a small
  16383->0 boundary residual;
- fine-tune offsets follow the expected +2 MHz baseline and do not revert
  during producer setup;
- every soak stage preserves enable/progression/state, heap, Wi-Fi liveness and
  teardown restoration;
- synthetic BitScrambler, sparse, PARLIO, pipeline and preview benchmarks have
  enough margin for the measured source rate;
- the bounded one-second real ring -> BitScrambler -> conditioner -> PARLIO
  benchmark has no overrun/drop/underrun and consumes at least 90% of the
  measured producer rate;
- an RF tone sweep separately establishes the tap bandwidth before any sparse
  factor is marked alias-safe.

For that sweep, step a constant-amplitude generator through positive and
negative offsets and run, for each point:

```text
TONE RESPONSE PROBE 0 <signed_offset_hz> <measured_rate_hz>
```

The device fills at least one complete ring, measures phase slope, coherence
and IQ magnitude, and emits one machine-readable `C5VRX_TONE_RESPONSE` record.
Repeat the sweep for modes 11 and 12 when comparing candidate taps. The host
cannot safely retune an arbitrary external generator, so changing the generator
is the only manual step; capture and classification on the C5 are automated.

Until the bandwidth sweep is complete, `CAPABILITIES` retains
`source_bandwidth_known=0` and `LIVE START` fails closed. For a lab-only look at
the implemented ring path, `LIVE EXPERIMENTAL START 0` is available and prints
`EXPERIMENTAL_RING_SOURCE_UNPROVEN`; its output is not production evidence.

After preserving the sweep data, explicitly import its result for the current
boot with:

```text
APPLY MEASURED BANDWIDTH <occupied_hz> <alias_safe_factor> CONFIRMED
```

This command is intentionally not a measurement: it is a guarded operator
assertion that the external RF sweep established both occupied bandwidth and
anti-alias filtering. It rejects unknown rates, factors other than 1/2/4/8 and
results that violate complex Nyquist. Retuning clears the imported result and
all RF-dependent capability gates. The implemented BitScrambler route is fixed
at /4, so it is selectable only when the confirmed factor is at least four.

After all gates pass, `LIVE START` automatically selects and starts the
implemented ring/BitScrambler route. It additionally requires the full
mode-0 staged soak, WBFM self-test, PARLIO benchmark and at least 20% synthetic
pipeline throughput margin, plus the one-second real-ring benchmark. Lower-tap
and sparse-CPU choices remain fail-closed
until their corresponding consumers are implemented and physically validated.
Then use `USB PREVIEW START`; disconnecting or stopping preview does not stop
PARLIO AV. Stop the receiver with `LIVE STOP` and preserve the complete console
log, especially ring overrun/drop statistics.
