# Precomputed pulse research on C5

C5VRX by Twotoz and the C5VRX contributors:
[source](https://github.com/Twotoz/C5VRX),
[official website and Discord invite](https://twotoz.github.io/C5VRX/).
These are temporary adaptations of the root keeper, not a working live writer.

The physical fast app measured a24us minimum H preparation and1703us minimum
first-EQ preparation. Normal preemption is included; neither minimum proves
worst-case timing. The hardware sample stream must remain autonomous. The
CPU should prepare/reuse pulses and adjust boundaries before their deadlines.

## H carrier bank

`research/sync_h_bank.py` compiles a temporary core. Its48-sample front porch
absorbs endpoint residual; the188-sample constant-frequency tip is copied from
a bank selected by the received endpoint phase. The following burst/back porch
and active IQ are unchanged. Masked or nonstandard phase maps retain the old
synthesizer. No production bank or writer was enabled.

The straightforward bank passes full keeper scenes at kp0.85 through0.55, but
fails kp0.50. Several separate H-only frequencies were tested there:

| H step at kp0.50 | Full-scene outcome |
| --- | --- |
| -7373 | PAL fade pulse lost |
| -7390 | continuous-loss gate fails |
| -7414 | PAL weak pulse lost |
| -7430 | full scenes pass |
| -7455 | continuous-loss gate fails |
| -7537 | cold NTSC pulse lost |

The-7430 candidate also passes the original transition matrix and a2621440-case
sweep whose two endpoints followed one continuous porch tone. A stronger
independent16x16 endpoint grid finds five failing rows at kp0.50, despite the
unchanged dynamic synthesizer passing. The candidate therefore remains
unqualified there. Do not loosen the external width or15-sample edge gates.

The phase fixture was tightened again: all32 tracker phases and0/63 LP state
extremes now start at the first fully generated pair, covering an unknown
real/synthetic boundary. The earlier fixture's512-sample prelude could converge
different initial states before the pulse. The current runner records hashes
and acceptance totals for its own named CSVs; distinguish these fixtures when
reading historical data.

Storage need not scale as256 copies of every pulse length. A constant carrier
can use one complete modular phase period plus a256-entry offset map. The six
trial tip steps together require262144 carrier bytes before wrap padding; the
fixed-1MHz blank step has a2048-sample period. This is a design calculation,
not an implemented or qualified V-pulse bank. Arbitrary blank frequency and
native-mask contexts still need explicit handling.

## First equalizing pulse

`research/sync_universal_tip.py` tries two separate temporary constructions:

- An all-state universal tip with12 construction-settling samples. Actual
  output gates stay unchanged. It passes the kp0.85 full scenes, but loses
  first-EQ pulses at kp0.55/0.50: the decoded edge can start16 samples late,
  beyond the15-sample gate. Combining the H bank does not fix this.
- A shorter actual-history prefix with the original strict code8 construction
  gate.16 bytes cause a bootstrap refusal in cold kp0.50.32 and64 bytes pass
  the full kp0.50 scenes; all-context qualification remains outstanding.

An output matrix pass is insufficient when `bootstrap_refused` is nonzero:
it can be the generic fallback rather than the proposed constructor succeeding.
In particular,0.5x needs explicitly bound unscaled reference-code ranking, as
the earlier `sync_reference_tip.py` experiment demonstrated. Its old source
needle predates the optimized token cache and must be adapted before rerunning.

The production prefix still reads128 bytes. Its intermediate deduplication
now merges equal phases because previous worker DAC does not enter the next
LUT address. The final ordered phase/DAC set and38400 tip outputs remain
byte-identical to the physical-J baseline. This exact simplification is already
committed; its actual C5 timing has not been measured.

## Acquisition and physical next step

`research/sync_coarse_scenes.py` reuses the exact coarse63 discovery builder
without importing the IQ simulator. All eight kp full keeper suites pass.
This strengthens the earlier80us-service replay, but production acquisition
is unchanged. TRACK can still exceed the outer192 evaluation budget inside
its pulse-width/search helpers; it needs bounded/resumable work and physical
timing before continuous service is accepted.

The next built images provide J stage profiles and manual read-only W timing.
The C5 disappeared from USB before the attempted flash, so no new physical
results may be attributed to them. Reconnect, flash the recorded image, capture
J/W and heap/error counters, then choose the next optimization from those
measurements. A private timing pass alone still does not deliver monitor lock.
