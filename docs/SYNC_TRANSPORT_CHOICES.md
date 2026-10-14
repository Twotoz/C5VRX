# Live real-snow keeper: transport and template choices

C5VRX by Twotoz and the C5VRX contributors. This review addresses
[PR212's streaming gates](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6156734030)
against the current root firmware, including the new OUTPUT0.5x setting.
It does not enable a keeper or certify a live memory writer.

## Recommendation

Keep the existing hardware sample stream and finish the bounded shared-raw
scratch callback first. It is already host-tested against the real candidate;
the actual read-only transport probe exposes its remaining proof inputs.
Replacing that path with shadow TX descriptors exchanges the raw-observer
problem for a larger descriptor-publication/lifetime problem. It does not
make DMA ownership disappear.

Use independent raw/transport ownership. A DC trial legitimately holds analog
PHY ownership for milliseconds, while H/V timing must coast and repair. The
actual read-only capture already uses separate scheduler ownership and does
not wait for the analog PHY mutex. Gain/DC/retune changes invalidate source
evidence that spans them; a small same-channel DC nudge need not erase the
previously qualified camera clock. All halted transport/LUT changes must
advertise the live generation unavailable before the first hardware mutation.

## Compared storage designs

| Design | Extra data work/storage | What it simplifies | Remaining obligations |
| --- | --- | --- | --- |
| Owned pulse scratch, then complete shared-raw commit | At most1280bytes scratch; only admitted pulse bytes copied | Existing actual TX chain and frontier API remain applicable | Exclude every raw reader, cache-line neighbors and RX overwrite; prove whole commit deadline and register publication/in-flight bound |
| Full shadow TX ring |32768bytes; full-stream CPU copy adds40MB/s reads and40MB/s writes | RX stays immutable; observers cannot see repairs | Source freshness, TX data publication and lifetime still required; e913's33636-byte internal heap leaves only868bytes before task/workspace needs |
| Two shadow TX blocks |2×4092bytes plus descriptors; same full-stream copy work during sustained loss | Smaller RAM allocation; RX remains immutable | Retire a block only after its last TX memory read; publish buffer pointer before descriptor prefetch; sustain copy plus detection/synthesis under real load |
| Sparse original-IQ/scratch scatter chain | Only repair payload plus small descriptors | RX stays immutable; original source evidence needs no repair-taint ledger | Prebuild whole chain; publish one aligned32-bit predecessor link before prefetch; retire descriptors/scratch after TX completion; variable geometry, context reset and pair continuity need a new adapter |

Full-copy traffic is a real cost, but is **not proved impossible** by adding
CPU traffic to the GDMA AHB bandwidth: the CPU's SRAM/interconnect path might
use a separate port. The shared bottleneck needs hardware evidence. Likewise,
two buffers could be viable despite the full ring's RAM cost. They should not
be presented as a proven improvement before measuring their complete work.

For sparse scatter, changing several fields in an already reachable descriptor
is unsafe. Prepare immutable replacement descriptors and payload first, then
publish a single predecessor `next` pointer whose descriptor is proved not yet
prefetched. A32-bit store is not, by itself, proof that DMA did not already
fetch the old descriptor or that cache publication is atomic at the consumer.
The current fixed nine-node TX layout/progress helper cannot simply be reused
after inserting arbitrary segments. Preserve the exact byte sequence and
BitScrambler state across every segment, including odd pair boundaries.

Every immutable-RX alternative also needs separate views for original camera
evidence and the **actual transmitted decoder history**. The current candidate
reads one raw ring for both. Its first-EQ predictor replays the preceding128
samples, which can include a previously repaired V slot. Replaying untouched
RX there after sending a shadow repair would predict the wrong tracker/LP
state. A small retained transmitted-history buffer or explicit lookup through
published shadow segments can solve this, but it is an algorithm/API change,
not just a different descriptor pointer. Keep those histories generation-tagged
and retire them only after both TX and the predictor no longer need them.

## Full-loss bandwidth includes the vertical train

`research/sync_output_transition.py` derives these figures from the actual
candidate geometry, rather than counting horizontal repairs alone:

| Mode | Regular H/s | V slots/s | Repair payload/s | Worst64-byte-rounded raw commit footprint/s |
| --- | ---: | ---: | ---: | ---: |
| PAL |15250|750|4.559MB|5.888MB|
| NTSC |15194.805|1078.921|4.9574MB|6.3124MB|

This includes a236-byte H/front-porch interval and the whole half-line V slot.
Scratch preparation writes, scratch reads and the rounded raw write together
account for roughly15.0/16.23MB/s before source reads, searches, code/cache and
observer work. Rounded-cache traffic is a conservative footprint calculation;
actual internal-SRAM cache policy must determine what really reaches memory.
None of these rates establishes CPU cycles, memory contention or WCET.

## Output and transition evidence

The new `research/sync_output_transition.c` directly includes the actual
candidate synthesis, uses the actual `output_entry()` mapped worker/controller
tables, and independently executes the BS arithmetic for LP and non-LP.
It covers all eight Kp tables, both programs, all ten OUTPUT indices including
index9=0.5x, both standards, H/EQ/broad/first-EQ, both pair parities, all32
initial tracker states and two initial level extremes. The eight original
phase prefixes are deterministic strong-cell -1MHz porch cases.

This is a **transition audit**, not the full scene acceptance suite. It lacks
weak/noisy prefixes, arbitrary camera histories, clock drift, real loaded DAC
voltages and monitor behavior. Non-LP rows are diagnostic: the production
candidate's first-EQ state predictor currently assumes the LP recurrence.
All original IQ outside each private repair interval is checked byte-exact.

The reproducible CSV/JSON contains1280 context/shape rows and1310720 decode
cases. All meet the fixed normalized-crossing width/edge window in these
limited scenes. This deliberately does not erase the failures under the
absolute/mapped thresholds, or promote non-LP diagnostic rows to supported
keeper operation. All256 first-EQ bootstrap attempts at0.5x refuse internally
before the generic fallback; both decoder-program audits retain that fact.

Three distinct thresholds expose a real context problem:

* The existing absolute DAC≤8 criterion works at1x but cannot recognize0.5x
  sync: the new mapping's minimum DAC code is11.
* Blindly mapping8 through `output_entry()` fails for LP at amplified outputs.
  Clipping maps that ceiling to0, but the LP's integer carry can produce1.
  Its recurrence is `y'=floor(y/2)+floor(x/2)+carry`, with carry0or1. Even a
  derived settled ceiling `mapped8+1` is not a guarantee of a≤15-sample edge.
* A fixed normalized crossing, `map0+round(8*(map21-map0)/21)`, preserves the
  baseline absolute8 threshold as an8/21 sync-to-porch fraction
  at the configured -1MHz porch target. It provides a consistent
  comparison across these mappings; it is not a fitted waveform threshold
  or a claim that any physical monitor uses that fraction.

The current first-EQ implementation refuses all tested0.5x bootstrap attempts
because its internal regular-token ceiling remains the literal8. Its generic
fallback succeeds on these simple matched prefixes; that does not establish
recovery from arbitrary weak/noisy decoder states. Fixing this requires an
explicit verified output/program context and a waveform criterion tied to
sync depth/porch, followed by the real cold/weak/return scene matrix. Do not
replace a constant and label that physical acceptance.

Private pulse edits can also change decoded samples after the raw handoff
even while subsequent IQ is unchanged. The audit records the last differing
sample within a256-sample suffix against the untouched blank-prefix baseline.
This diagnostic measures tracker/LP settling, not lost moving-picture detail.
The full scene tests must separately require the intended active-DAC fidelity
at every admitted handoff.

## Prewarming that is actually useful

Build `s_cell` before starting the timed service. Its256×256 search and1KiB
temporary array must not occur on the first lost pulse. The existing exact
`sync_synth_next()` DDA already removes per-byte division and64-bit arithmetic;
two divisions occur at initialization. Retaining that exact cheap generator
into owned scratch is a strong baseline before introducing large banks.

An unqualified fixed raw template is insufficient. Incoming phase, tracker/LP
state, endpoint residual, porch estimate, actual Kp and OUTPUT all matter.
A bank with only32 starting phases and H/EQ/broad/first-EQ shapes costs130432
bytes for one context;256 phases cost1043456bytes. Both exceed the observed
free internal heap. Full phase×residual banks are much larger. Flash storage
can help only after proving that the selected templates preserve real decoded
transitions, and flash/cache access must remain outside the bounded commit.

The smallest optimization is to prewarm phase-cell/token choices and stable
interiors, then retain exact dynamic boundary synthesis/first-EQ state proof.
Validate the actual whole pulse and its following real DAC samples; a steady
carrier sweep is not enough. Keep separate output/program generation tags,
and refuse stale prepared pulses after any transport setting changes.

## Next safe buildable step

The explicit manual console `J` probe now implements that first step. It
allocates8832bytes of independent internal DMA-capable RAM:4096-byte private
ring,1280-byte workspace,2048-byte verified LUT copy,1344-byte destination and
64-byte tail guard. It snapshots `s_lut_live` and actual verified Kp/LP/OUTPUT
context under a brief scheduler lease; it never touches the hardware LUT port.
The real candidate prewarms its phase cells and prepares H/EQ/broad/first-EQ
into owned workspace, then measures aligned refresh/copy/flush into the private
destination. Only that copy envelope masks interrupts. Expensive synthesis,
seeding, context checks and logging keep interrupts normal. Non-LP first-EQ is
explicitly ineligible because the predictor assumes LP. Context changes abort.

`J` has no automatic trigger or persistent state and does not start a keeper.
It reports first-call prewarm, per-shape preparation cycle/time maxima, private
copy envelope cycle/time maxima, actual cache return codes, CPU clock and
bootstrap refusals. `ESP_ERR_NOT_SUPPORTED` is accepted only for the allocated
noncacheable internal-SRAM case; aligned cacheable operations use no invalid
M2C+UNALIGNED combination. Source and destination guards are verified outside
the critical section. `valid` means private preparation/copy/context checks,
not CVBS waveform acceptance.

The actual adapter's extracted-source host test covers balanced scheduler and
IRQ ownership, allocation/free, unchanged live raw/LUT buffers, context changes
during preparation, cache failures, private-only destinations and tail guards.
The portable actual-core API test covers640 preparations across all Kp/output
maps and refuses external executors, overlapping buffers and invalid contexts.
The pinned actual firmware builds. Physical `J` measurements remain pending;
no board was available during implementation. Logs explicitly state
`irq_latency_measured=0` and `live_wcet_proven=0`. A measured private critical
section envelope is not an IRQ dispatch-jitter measurement or a WCET proof.

Measure on the real C5 under USB/native/gain/DC load, then measure complete
detector-only service turns and source age using actual frontiers. Host elapsed
times and static instruction counts remain insufficient.

The live executor still needs every raw-reader entry/exit covered by one
independent ownership mechanism, a coherent fresh memory-frontier capture,
proven publication/outstanding-prefix bound and a justified short IRQ-masked
commit WCET. Synthesis/search/logging remain outside that section. The callback
must refuse before its first store or finish the whole pulse; it cannot undo
a partly transmitted replacement. Keep `mem_writes_safe=0` until those proofs
exist, then perform opt-in terminated-CVBS/affected-LCD tests before automatic
enablement. This is a usable-display-range feature, not measured RF gain.
