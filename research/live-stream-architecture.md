# Prepared live RF streaming architecture

## Status

**PROVEN IN SOFTWARE / BUILD TESTED**

- `c5vrx_rf_source_t` block ownership/acquire/release interface;
- bounded four-block producer/consumer queue and high-water accounting;
- separate source and processing tasks with drop/backpressure counters;
- persistent sample-domain route through the existing 4:1 C5 BitScrambler WBFM
  transform (no CPU `atan2` in the live path);
- configurable DC tracking, bias, polarity, Q8 gain, optional one-pole filter,
  and output clamp;
- two-buffer 20 MS/s PARLIO/GDMA live sample sink;
- finite vendor dump adapter and `NEARLIVE START` diagnostic;
- source/output underruns, dropped blocks, discontinuities, stage timing,
  achieved rate, WBFM/CVBS ranges, occupancy and high-water diagnostics.

**PHYSICAL TEST PENDING**

- whether finite chained dumps contain useful analog-FPV I/Q;
- actual sample rate and gaps;
- RF-to-CVBS polarity, bias, gain, filtering and voltage calibration;
- sustained PARLIO timing and passive DAC levels into the real 75-ohm input.

**CONTINUOUS RF PRODUCER UNKNOWN**

The only missing module is a source with `kind = C5VRX_RF_SOURCE_CONTINUOUS`
that returns phase-bearing blocks without gaps. It must implement `acquire` and
`release`; all downstream buffering, hardware WBFM, conditioning, output and
diagnostics remain unchanged.

Key unresolved question:

> Can the ESP32-C5 provide a sufficiently continuous, phase-bearing RF stream
> for real-time WBFM demodulation?

`NEARLIVE START` repeatedly uses the recovered finite vendor dump. Every block
is flagged discontinuous and logs identify the mode as
`FINITE_CHAINED_NOT_CONTINUOUS`. It is an experiment, never evidence of a true
continuous producer.

The route deliberately has no full-frame buffer:

```text
RF source -> 4-block queue -> BitScrambler 4:1 -> conditioner
          -> two PARLIO blocks -> 6-bit resistor DAC
```

Calibration defaults are placeholders. Change the Kconfig Q8 bias/gain,
polarity, filter shift and clamps only from measurements of a controlled VTX.
