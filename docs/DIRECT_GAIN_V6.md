# Direct Gain V6 investigation

C5VRX by Twotoz and the C5VRX contributors. This extends Direct Gain V5
(`main/gain.c`, `main/receiver.c`), the earlier Direct Gain V3 tuple model,
the archived gain/DC work, and the 2026-10-13 walks. It responds to PR212
[6166986202](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6166986202)
and [6167109293](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6167109293).
All seven PR discussion comments were read, including output AUTO/floor,
flywheel deadlines, output A/B/C and the priority order; both review/inline
comment endpoints were empty at this review.

## Firmware V6 carrier memory (2026-10-18)

**Direct Gain V6 is now the firmware gain owner.** It is V5 (measurement,
healthy band, continuous tracking, overload safety) plus a carrier memory.
It does not implement the research classifier below; that remains research.

Board readout 2026-10-18 (PR212 `6285771`, A1 NTSC, AFC locked, strong
camera): field traces showed G21 -> G63 after each fade with up to 742 per
mille clipping on return, then a staged walk back through G58/G53/G38/...
`GAIN` counters (`writes=16 fade_holds=0`) showed why the 10-ms fade hold
rarely acted: the observer rebinds the model on every AFC offset step and
PHY lease (`phy_generation`, `s_control_epoch`), and the rebind cleared
`last_healthy_us` together with the whole tracking state.

V6 keeps the last healthy, non-stuck gain (`anchor_gain`) and the fade/carrier
timestamps across tracking resets when channel, filter code/skirt/BW, lane, IQ
scale and the complete gain table are unchanged; the AFC offset may differ.
The learned map keeps its exact V5 provenance rules. With a remembered gain,
an empty window after the 10-ms hold climbs `DG6_CLIMB_STEP` = 8 indices every
`DG6_CLIMB_US` = 3 ms (a carrier that is really gone still reaches the
maximum, ~25 ms later). Saturation above the remembered gain returns to it in
one write, without the 1-ms burst gate; at or below it V5's burst gate and
staged recovery are unchanged. A Wi-Fi burst during a climb therefore costs
one write to a known-good gain.

Host model (`test/test_gain_fade.c`, V5 -> V6): -40 dB fades of 10/30 ms
picture back 1.8 -> 0.0 ms after the carrier; sustained -22 dB step
20.4 -> 13.8 ms; VTX off still ends at G83; all 28 checks pass.
`test/test_gain_v6_memory.c` (85 checks) covers rebind retention, channel
change, bounded climb, one-write return, maximum reach, stuck DC and the
retained burst gate. This model is rough and monotonic; no board/range result
is claimed until a physical A/B.

Manual mode (operator request): saved `gain_mode` (console `G`, menu GAIN)
selects AUTO (V6), MAX (table maximum) or fixed G78..G24. Fixed modes skip
`dg_tick`; paused owners (calibration, scan) still write and the mode is
re-applied afterwards. AUTO is the default.

## Research result (before the firmware V6 above)

**V5 remained the firmware gain owner at this research stage.** The new V6 implementation is an
experimental decision helper in `research/gain_v6_policy.h`, default OFF,
with no production caller, register access, menu item or NVS state. Enabling
its macro only enables the research policy; it does not enable board V6.

The simplest justified change is to separate **ADC placement** from **source
identity**. A healthy envelope is useful placement evidence, but cannot by
itself establish a carrier, receiver noise or valid DCO. Reclassifying a stuck
point as ordinary receiver noise recreates the rejected max-gain loop.

The helper preserves V5's candidate selection and sustained overload gate
through an explicit V5 proposal input. It adds context admission, source
classification and transition verification. There is no second hardware
gain owner, new per-sample pass, filter switch or native-AGC dependency.

## Classification and decisions

| Evidence | Classification | Experimental decision |
|---|---|---|
| Missing/stale/unsettled/changed source context | Unknown | Hold |
| Sustained physical clipping, V5 proposes lower tuple | Overload | Accept V5 safety drop; bypass ordinary cooldown |
| Repeated point and span <=2 codes | Stuck/DC | Hold; request DC diagnosis, never force maximum |
| Recent healthy genuine carrier, same context, weak interval <=10 ms | Fade | Hold prior gain |
| Genuine source sync, observable span, verified DC residual | Carrier | Healthy band is strictly zero-write |
| Independent positive noise evidence, observable span, verified DC residual | Noise | Evaluate conservative ordinary proposal |
| No sync without positive noise evidence | Unknown | Hold; absence of sync is not noise proof |

Input admission compares measured and current gain/control/PHY/table,
frequency/offset, filter/BW/skirt, lane/scale, packed physical tuple and actual
DC pair. Observations must be <=2 ms old and begin at least 500 us after the
last write (or the longer measured settling guard). The actual packed tuple
must match. Strong/fast FM is not rejected on low phase coherence.

Ordinary proposals need confidence >=3 at both measured map entries,
combined uncertainty <=350 per mille, improved predicted placement and
conservative P95 headroom, plus explicitly verified map context. Existing
map confidence alone does not prove matching frequency/filter/DC conditions.
These are experimental bounds, not hardware
validated optimal thresholds. Low-confidence preset/fine-step priors do not
authorize a jump. This deliberately exposes insufficient map coverage rather
than assuming RF/BB/fine order equals measured gain.

The sole future owner must acknowledge a verified apply with actual target
context. Fresh target observations accept improved span/placement without
new clipping; two distinct harmful observations or a 10-ms validation timeout
request rollback. Unchanged/repeated samples do not count twice. Failed
destinations get penalties and 200-ms cooldown. Independent context changes
cancel the trial; the policy must never undo another owner's channel/filter/
DC change. Sustained overload retains priority over normal validation.
These are decision contracts, **not verified hardware rollback or timing**.
The timeout is acted on only with a fresh admitted observation; stale or
missing input cannot authorize an otherwise unsafe write. Carrier trials
cannot be accepted from better envelope placement after losing genuine
carrier evidence.

## Original capture and log replay

`research/gain_v6_replay.py` links actual `main/gain.c`/`arc_phy.c`, checks
the original IQ FNV checksums, and measures each complete 256-byte window
at its recorded gain. The deterministic result is
`research/data/gain_v6_replay.json`, with source/data SHA256 hashes.

| Original recordings | Complete windows | Result |
|---|---:|---|
| Four VTX-off G59 snapshots | 124 | All 124 classified stuck; DC diagnosis, zero V6 gain applies |
| Six weak G73–75 snapshots | 186 | No stuck windows; all unknown because synchronized DC/source context is absent |

The independent V5 proposals in weak snapshots total 13/186 windows. Those
are proposals from a reset controller at the recorded gain, **not writes in
the actual walk**, and cannot be compared with a closed-loop V6 trajectory.
Both controllers are evaluated on original bytes; no alternate-gain IQ is
invented. The V6 hold on incomplete evidence is a conservative limitation,
not proof of better weak-signal reception.

Original aggregate logs independently reproduce the failure modes:

- `walk_serial.log`: 165/200 diagnostic rows at G59–60; median observed
  write rate 0/s. This establishes parking, not its eventual recovery.
- `walk2_stuck_misfire.log`: median 126.0/s, maximum 388.3/s.
- `walk3_gain_loop.log`: maximum 1464.8/s, consistent with the documented
  max-gain/overload loop. No new V6 action is simulated from these counters.

An archived actual learned `GAINMAP v=4` contains 14 entries G20–33 only,
including nonmonotonic measured power (G28=579, G29=516). It has no coverage
of G59–75 and no synchronized filter/DC qualification; it cannot calibrate
the observed weak/floor failures. The measured table therefore does not
justify a monotonic gain-index counterfactual.

There are no synchronized multi-gain, fixed-RF-input captures in this set.
The existing `test_gain_fade.c` is a useful regression model, explicitly
rough/monotonic rather than a calibrated C5 transfer model. Its gain equation
does not predict per-gain DCO, folding or transition artifacts accurately
enough to certify V6. A single-gain replay cannot answer whether G59 should
become G65/G75/G83, or estimate changed noise figure/luma SINAD.

## Tests and remaining integration

`test/test_gain_v6.c` links the production measurement/controller and tests
default-OFF delegation; 1000 healthy zero-write decisions; every context
field and actual pair/tuple admission; stale/unsettled samples; DC/noise/
carrier/fade distinctions; 1–10-ms fade holds; map confidence; failed apply;
fresh validation; two-window rollback; timeout; context-change cancellation;
and actual V5 1-ms interference-burst/overload behavior. Artificial boundary
fixtures verify policy only, not RF performance or useful picture recovery.

Reproduce with pinned IDF 6.0.2:

```sh
python research/gain_v6_replay.py --source-revision fc9b8bd-plus-map-context-v5 \
  --output research/data/gain_v6_replay.json
```

Before a live optional V6 caller is justified:

1. Publish a synchronized, fresh **DC residual status**, matched to actual
   pair/gain/filter/PHY context. Existing pair readback proves a register
   value, and table validity proves provenance; neither proves residual DC.
   Strong-circle live trim helps identifiable carriers but cannot qualify
   arbitrary weak noise or a quantizer floor. Near-zero-CFO/partial-circle
   identifiability and irreversibly folded Q4 remain limits.
2. Define independent positive noise evidence. Do not derive it from missing
   camera sync, high coherence or the envelope C/N table alone. A trustworthy
   noise baseline or controlled source transition is needed for that claim.
3. Wire proposal/apply acknowledgment into the existing receiver ownership
   path. A rejected V5 proposal must not leave its state ahead of the actual
   hardware. Do not duplicate V5's whole controller on a small task stack;
   refactor proposal/commit semantics with actual-source ownership tests.
   The current V6 helper performs no mutations to V5's map/controller.
   `gain_map_preset.h` currently has **zero measured boards and all-zero
   entries**. A new board's confidence-1 anchor cannot satisfy the V6 gate;
   the unit test explicitly verifies this acquisition dead end. Safe bounded
   physical fine-step exploration needs proof of per-tuple DC/readback and
   independent post-write validation. Relaxing the confidence gate alone is
   not an evidence-backed fix.
   The transfer-context map bug found during this investigation is fixed
   separately below. That fix does not supply residual-qualified DC status.
4. Acquire repeated per-gain/context IQ under identical conducted input and
   fixed attenuation, including actual settle windows and readback; replay
   healthy carrier, VTX-off, DC floor, 1–10-ms fades, sustained attenuation,
   weak sync and abrupt strong return. Compare useful detail/sync uptime,
   clip/occupancy, writes, tuple choices and recovery latency separately.
5. Opt-in physical A/B against V5 and best fixed configuration with verified
   rollback. Board timing, display acceptance and RF-range gains remain open.

Holding unknown input indefinitely may sacrifice acquisition. This prototype
does not solve that by pumping gain; the missing observability must be made
explicit and tested before replacing the default controller.

## Implemented V5 gain-map provenance fix

The investigation found two concrete source bugs independent of adopting V6:
`dg_reset` retained learned ratios whenever table spans matched, even after
changing channel/filter; and a cached map's frequency was checked only at
startup, then not rechecked on later imports. The calibration saver could
also label an older model with whatever frequency was current when saving.

The production fix binds the observer's model to frequency, signed offset,
filter code/skirt/BW, IQ lane and scale, and the full physical tuple table
(spans, counters, RF codes, maximum and band). Every reset/import verifies
that provenance. Same-context tracking/native handoff retains learned ratios
and uncertainty. Changed contexts start with local priors; a stored A map can
be reused after A→B→A only when the current context matches A again.

The gain-map cache version becomes **5**; untagged version-4 cache data is
ignored because its missing context cannot be reconstructed. Channel,
standard, OUTPUT and other settings are unchanged. This may require learning
a new map; no disruptive map-calibration sweep is introduced.

`receiver_export_gain_map()` copies the sole observer's model with short task
exclusion and exact bound context/epoch checks. It never waits for the PHY
or does I2C/NVS work while excluding tasks. A nonblocking actuator lease
also rejects another `phy_try_actuator` owner, which `phy_busy()` alone
cannot detect; last-write freshness prevents export during DC settling.
The saver persists that tagged
snapshot and atomically publishes the complete cache; it never substitutes
current RF labels. The 30-second limit and unchanged-blob wear guard remain.
Actual hardware worst-case export time remains a measurement gate.

`test/test_gain_map_context.py` compiles actual core code, receiver bind/
export functions, and the calibration saver against mocked hardware/task/NVS
boundaries. It checks each context field and physical-table component,
unchanged-context retention, A→B→A, old caches, mismatched labels, changed
context during snapshot, stale model refusal and unchanged-NVS suppression.
Pinned IDF 6.0.2 tests pass, alongside gain-fade, gain-preset, native ownership
and existing settings compatibility tests. No physical range gain is claimed.
