# PR 212 comment follow-up

C5VRX by Twotoz and the C5VRX contributors. Reviewed against `fc9b8bd`
on 2026-10-14: all six conversation comments, the reviews endpoint, inline
comments endpoint and GraphQL review threads. There are no inline review
threads. The automated summary refers to historical commit `247a24e`; it
does not validate the current head.

## Priority and acceptance

The latest [priority comment](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6167109293)
orders RF work as usable-IQ gain/DC policy, physical AFC centering,
wide-VTX/filter matching and safe sampling adjustment. The sync keeper and
output experiments address usable display reception separately. Conditional
simulation losses overlap; they cannot be added or reported as measured range.

| Comment | Concrete follow-up | Current evidence and remaining acceptance |
| --- | --- | --- |
| [AUTO OUTPUT floor](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6156589968) | Optional manual/AUTO mode, persistent user minimum and optional ceiling, separate volatile selected level; sync/porch-qualified acquisition-time updates with headroom, hysteresis, cooldown and hold reasons. | Not implemented. Existing manual levels persist. A multiplier floor cannot guarantee loaded sync voltage; loaded 75-ohm measurements and no-relock tests remain necessary. |
| [Flywheel review](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6156734030) | Full kp/program/output waveform matrix; prewarmed templates with verified phase/LP handoff; original-only camera qualification; safe complete-pulse commits; measured worst-case timing and affected-LCD tests. | Host 1x U85Q-LP keeper passes eight kp levels at nominal and +/-50 ppm, including callback admission and cold-start cases. This does not cover all output/program combinations. Live writes remain disabled pending memory/reader/deadline proof. |
| [Identical-IQ A/B/C](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6166374209) | Replay the same input through bit-exact 1x, actual 0.5x LUT and timing-qualified sync-priority processing; compare actual decoded content, clipping and sync/headroom. | Manual 0.5x is appended as setting 9, preserving earlier setting IDs and 1x default. It reduces digital span and sync depth; LCD improvement is unproved. Sync-priority needs timing segmentation and cannot be implemented by a shared per-code LUT alone. |
| [Direct Gain V6](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6166986202) | Classify carrier/noise/stuck-DC/fade/overload/unknown using fresh matched evidence. Retain V5 fast protection, healthy zero-write behavior and one gain owner. Investigate predictive tuple changes with validation, rollback, penalties and cooldown. | Experimental investigation first; V5 stays default. Current receiver lacks a public residual-qualified DCO observation and a positive noise classifier. Unknown evidence must remain unknown. Replay at one gain cannot prove another gain's result. |
| [RF priorities and AUTO versus fixed](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6167109293) | Compare current AUTO against the best stable same-hardware gain/DC/AFC/filter/kp/LP configuration, then V5 versus experimental V6. Investigate wide-profile rejection and DCO/filter sharing explicitly. | Conducted attenuation A/B/A, drift/offset and narrow/wide-VTX measurements remain outstanding. Existing gates must not be loosened using a blanket C/N correction or biased weak-carrier retunes. |

## Results from this review round

- Fixed a concrete V5 gain-map provenance bug: learned ratios could survive
  channel/filter changes and the saver could relabel an old model with the
  current frequency. Version-5 maps now bind the complete physical table and
  transfer context; every reset/import/export checks that binding. See
  [Direct Gain V6 investigation](DIRECT_GAIN_V6.md). V6 remains research-only:
  existing weak-signal recordings lack synchronized DC evidence and calibrated
  alternate-gain outcomes.
- Added passive `PROFILE_GATES` diagnostics to distinguish missing C/N from
  low C/N and amplitude/clip/gain/lock rejection. Eligibility thresholds and
  AFC/profile policy stay unchanged; physical drift and filter-AM bias still
  need controlled measurements. `AFC_EVIDENCE` separates sticky lock from
  last-qualified porch residual, timestamped history coverage, age and hold
  reasons. These are porch-based observations, not independent RF truth.
- Replayed identical IQ across eight kp levels, both programs, both parities
  and real/synthetic sources. See [A/B/C evidence](OUTPUT_ABC_EVIDENCE.md).
  The malformed PAL counterexample is retained separately from a corrected
  source: the latter passes all 16 keeper kp/parity cases without active IQ
  or stable active DAC changes. This is not a display-lock guarantee.
- The [transition matrix](SYNC_TRANSPORT_CHOICES.md) exposes the fixed
  cold-start sync threshold's incompatibility with 0.5x output, and LP state
  differences across output settings. No blanket keeper enable follows from
  the narrower 1x tests. Manual `J` now measures preparation and private copy
  envelopes in owned RAM using the actual verified LUT; it never writes live
  IQ. Physical measurements are pending;
  live DMA publication and actual worst-case deadlines remain acceptance gates.

## Measurement contract

Use a recorded moving pattern and repeat/randomize A/B/A over the last
8–12 dB in 0.5–1 dB attenuation steps, with the same source and hardware.
Record original IQ/context, gain writes and RF/BB/fine transitions, settling,
DC/span/clipping, offset, profile/kp/LP, genuine sync, decoded detail,
terminated CVBS and display lock as distinct measurements. Label model-based
counterfactuals separately from captured actions and physical results.

For output A/B/C, hold RF/IQ constant; measure sync tip, back porch, white,
peak-to-peak voltage and clipping at the load. Count false crossings and LCD
drop/recovery separately. A decoder model is not a closed-loop voltage sensor.
Do not credit an output restart's reacquisition to the output mapping.

No proposal authorizes replacing received snow with black content, inventing
RF gain, continuously restarting the output during fades, or enabling an
unvalidated controller by default. Issues 201 and 213 remain open until their
physical acceptance requirements are met.
