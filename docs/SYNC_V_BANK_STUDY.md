# Periodic ordinary V-slot preparation study

C5VRX by Twotoz and the C5VRX contributors: [source](https://github.com/Twotoz/C5VRX), [official website and Discord invite](https://twotoz.github.io/C5VRX/). This extends the root keeper and its actual U85Q-LP decoder fixtures. It is an isolated host experiment, not a live writer or a measured C5 optimization.

`research/sync_v_bank.py` builds a temporary core from the frozen pre-integration snapshot by default (`--core` can select a different snapshot). It does not edit production sources. The historical V-only experiment kept firstEQ history-dependent and H dynamic; its ordinary EQ and broad slots used:

1. A constant-frequency tip copied from one complete modular phase period, beginning at the actual received endpoint plus the existing kp-dependent phase lead.
2. Forty-eight DDS blank samples that distribute the shortest endpoint residual.
3. A constant-frequency blank tail ending at the real next sample's phase minus its expected blank step.

The experiment requires the unmasked default Phase8 map, a verified kp/LUT context, and exactly the tested blank step of -1632 Q8 Phase8 bins/sample. Other maps, native masks and other blank steps retain the dynamic implementation. The actual OUTPUT map is tested through the real worker/controller words; this study makes no U85Q/non-LP claim. Native-mask support is not inferred from the unmasked results.

The five current tip steps have periods32768/65536/32768/32768/65536 samples, totaling229376 bytes. The blank carrier needs2048 bytes. A sixth -7430 carrier adds32768 bytes. The original host prototype allocates padded arrays and generates them at startup. Subsequent production integration now generates the packed flash header offline; its264448 bytes also include256 canonical strong cells. H uses it in all qualified canonical contexts; ordinary V only at kp>=0.60. No live writer is enabled.

Modular inverses select the carrier offset directly. Tip entry rounding is at most one Q8 unit (1/256 of a Phase8 bin); the blank tail endpoint is exactly representable. The existing low-kp phase lead is preserved. There is no arbitrary phase reset at a table boundary. Each periodic section needs at most two contiguous copies when it wraps.

## Actual bank coverage and canonical V blank

Coverage instrumentation exposed an important gap in the first full-scene runs. With learned blanking, kp.85 and.50 execute **zero** bank calls: CFO produces blank steps such as-1312/-1328/-1344, and cold generation can still have a learned step0. Their passing full scenes do not qualify the bank. The independent private transition fixtures explicitly use-1632, so their candidate coverage is unaffected.

A separate temporary variant now passes all eight full unity scene suites with real bank coverage. Only the V-slot synthesizer's blank and next-step arguments are set to the canonical output blank-1632; source measurement, camera clocks, H front porch, received active content and first-EQ tip construction stay unchanged. Ordinary-bank phase lead uses the **emitted** preceding blank. Keeping the cold learned f.blank_q4=0 as that lead instead fails cold PAL at kp.55(h639 EQ) and.50(h632 broad); that negative run is retained in `sync_v_bank_canonical_coverage.json`.

| kp | Accepted ordinary banks | Deliberate executor refusal | Dynamic firstEQ calls | Native-mask fallbacks |
| --- | ---: | ---: | ---: | ---: |
| .85/.80/.75/.70/.65, each | 1152 | 1 | 84 | 108 |
| .60/.55/.50, each | 2230 | 1 | 147 | 270 |

The single refusal is the existing explicit false-callback executor test. Every other eligible unmasked ordinary call takes the bank; fallback reason counters distinguish masked inputs and dynamic firstEQ. Failed assertions dump coverage before aborting, so negative cases are not hidden by a missing exit report. Artifacts are `sync_v_bank_canonical_emitted_high6.json` and `sync_v_bank_canonical_emitted_low2.json`. The separate0.5x full-scene run also passes all eight kp with identical accepted-bank and fallback counts; see `sync_v_bank_canonical_halfscale.json`.

If canonical V blank is later rejected, a packed period family for every learned q4 blank value[-128,-64] needs174816 bytes in flash (65 periods, maximum4096 samples), rather than4KiB runtime generation per update. This is a storage calculation, not a tested family.

## Results and limits

The later exact-period H experiment keeps the original tip steps except the
separately qualified kp0.50 H step-7430. The original-7373 periodic H still
fails192 independent boundary cases at kp0.50; exact phase alone did not fix
it. The-7430 candidate passes all2621440 H boundary cases and the complete
kp0.50 scene suites at unity/0.5x and +/-50ppm, with44642 accepted H calls and
2230 accepted canonical-V calls per suite. The higher seven kp scene suites
retain their original H steps. Coverage counters distinguish dynamic firstEQ,
native masks and refused callbacks. These historical artifacts retain their
own source hashes in sync_hv_periodic_h7430*.json and the compressed boundary
CSVs. Production H and kp>=0.60 ordinary V now use the generated flash header;
physical results are tracked separately in FLYWHEEL_GOAL.md and board J logs.

The first unity run returned success for all eight kp full suites: cold PAL/NTSC, clean, faded and weak signals, injected prolonged-loss counters, camera return, both decoder parities, and active IQ/DAC preservation. Those historical runs had no bank/fallback coverage counters. Because the bank guard only accepts-1632 while RF CFO can produce a learned blank of-1344, a successful scene can use the old dynamic fallback instead. They are consequently whole-system regression results, **not proof that the bank handled all V repairs**. A separately instrumented canonical-V-blank run is required for that claim. The source core SHA256 was `2342726f4d8ae1b6b82c37633be34724aa4af4f5e3854e895923cfc14525b580`. The ordinary EQ/broad OUTPUT matrix passed327680/327680 LP cases at its unchanged normalized8/21 crossing fraction. No pulse-width threshold was fitted to the candidate.

A fresh reference-bound output run used core SHA256 `acdf35c16ce0688eb735878a5ad8c74fafb24f8d3c218a2006fb8eb88ea18d2a`; ordinary EQ/broad again passed327680/327680 LP cases. The new reference binding matters to the unchanged first-EQ constructor, so these snapshots are recorded separately rather than treating old fallback rows as new-constructor evidence.

The stronger fixture `research/sync_v_bank_phase.c` independently varies the real entry and exit phases over a16x16 grid. It starts every one of32 tracker phases at the first generated pair and tests both parities and LP state extremes x=y=0/63. It covers both standards, ordinary EQ/broad, and all ten OUTPUT settings.

| kp | Dynamic accepted | Periodic accepted | Cases | Rows where periodic loses accepted cases |
| --- | ---: | ---: | ---: | ---: |
| .85/.80/.75/.70/.65/.60, each | 1310720 | 1310720 | 1310720 | 0 |
| .55 | 1282823 | 1284672 | 1310720 | 45 |
| .50 | 1231111 | 1231872 | 1310720 | 185 |

Every case passes at kp>=.60:7864320/7864320. The .55/.50 aggregate increase does **not** certify them: some previously accepted boundary states fail. For example, .55,1x,NTSC EQ, grid seed64 (entry phase60, exit4) drops119/128?112/128. Those contexts remain unqualified for an unconditional bank. The baseline already fails other unknown-state cases, which are retained rather than hidden. Full real-prefix scenes passing at those kp does not erase the boundary counterexamples.

For each fixed tracker phase, phase/token evolution is independent of x/y, and the actual LP recurrence is monotone in initial x/y. The tested equal extremes bound all independent initial x,y in[0,63] pointwise. Low samples and accepted run lengths are monotone under that ordering, so passing both extremes also bounds mixed states in the fully passing rows. This is a digital waveform argument, not a loaded DAC or monitor-lock result.

A second, explicitly qualified fixture moves the unknown-state boundary64 blank bytes earlier, keeping the independent entry/exit grid and every other gate unchanged. All10485760 cases pass at all eight kp, including .55/.50. This fixture uses actual-1MHz source blank (-1638 Q8); it does not assert that arbitrary weak IQ or any arbitrary64 bytes are blank. The unsupported all-unknown-at-tip counterexamples remain in the separate zero-history files.

A low2 fast path can consequently be considered when the actual preceding64 transmitted bytes are proved to be the qualified blank history (including known generated bytes), or when a bounded state calculation proves an equivalent entry envelope. Repairs are valid decoder history here, never camera-sync evidence. A phase-only64-byte set replay over65 near-blank steps[-1664,-1600],256 starting phases and both parities collapses in every tested case:33280 histories per low kp, at most93(.55)/95(.50) phase transitions per parity. `research/sync_v_prefix_work.c` hoists each raw-pair token and uses a direct singleton tail after merging. Its256-transition cap returns an explicitly unknown set on exhaustion. These are operation counts, not target cycle measurements. Phase collapse alone does not qualify arbitrary noisy histories: actual x/y bounds or a known-blank predicate must also justify the LP entry envelope.

For parity1, the last preceding mixed pair contains prospective generated byte0; a state predictor must use that prepared byte rather than stale raw[a]. This source-history replay requires fresh ownership/context checks and must not read an RX-overwritten interval.

The ordinary-V bank is therefore a promising candidate for kp>=.60 within this tested endpoint grid, rather than a proof over every possible endpoint/period offset. Keep .55/.50 dynamic until a state-qualified construction or a better bank passes the same boundaries. Do not relax width or15-sample entry gates. Repeating the low2 boundary study with the exact generated blank step-1632 passes2621440/2621440 cases. This is separately recorded in `sync_v_bank_generated_history64.json` and the `*_history64_step1632_*.csv.gz` files. Its16x16 endpoint grid does not enumerate every one of2048 blank-period offsets. A production predicate still needs that wider phase qualification, lifetime proof and physical timing.

A successful-commit record for a generated blank span, plus an exact64-byte period comparison and fresh RX-age/context admission, could make sustained-loss trains cheap. A refusal-tainted ledger alone cannot certify that the bytes were written. Dynamic firstEQ must seed the train: preserving its tip and replacing only its last112 blank samples with a48-sample bridge and64-byte periodic tail is a possible separate experiment, not implemented here. Alternatively a bounded actual decoder-state replay could admit its following slot. Neither route may treat repairs as source evidence.

The low2 qualified-prefix result is a path forward, not a production admission implementation. First-EQ timing remains separate work.

## Byte work and remaining proof

A1280-byte slot would require48 DDS iterations plus at most1232 copied carrier bytes, rather than1280 DDS iterations. A final implementation can emit those sections directly into the existing owned workspace; the prototype uses an intermediate local pulse for isolated direct/callback equivalence. Per-context lead/inverses and map qualification belong outside the pulse deadline. Flash/cache copy cost, task preemption, callback admission, final raw copy and cache publication still require physical timing. The previous93us minimum regular-V preparation does not establish the new cost, and no WCET is claimed.

Reproduce the historical guarded experiment (which can fall back) and mapped matrix with:

```sh
python research/sync_v_bank.py --output research/data/sync_v_bank_start.json --transition
```

Exercise actual bank coverage in full unity scenes with:

```sh
python research/sync_v_bank.py --canonical-v-blank --output NEW_canonical_coverage.json
```

`--learned-lead` reproduces the negative cold lead experiment; `--output-level 9` requests the0.5x scene context. Coverage explicitly records accepted/refused bank attempts and fallback reasons.

Reproduce the fresh mapped/independent boundary study without repeating scenes:

```sh
python research/sync_v_bank.py --kp --output research/data/sync_v_bank_reference.json --transition --phase-sweep
```

Add `--history 64` and a new output filename to reproduce the known-prefix boundary experiment. Its files are `sync_v_phase_start_history64_baseline.csv.gz` and `sync_v_phase_start_history64_bank.csv.gz`; the known-prefix JSON records that assumption explicitly.

The JSON files record the core snapshots. `sync_v_phase_start_baseline.csv.gz` and `sync_v_phase_start_bank.csv.gz` retain all81920 rows each; uncompressed CSV hashes are in the reference JSON. The historical artifacts are evidence for their recorded snapshots. A rerun against a changed core is a new experiment and must use a new output filename.
