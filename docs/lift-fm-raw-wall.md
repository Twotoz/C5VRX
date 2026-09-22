# LIFT-FM raw-Q4 wall

The Phase5-domain LIFT-FM backend is exact and fits two BitScrambler bundles.
The remaining problem is the live input boundary:

```text
raw Q4/I4 @ 40 MS/s
        ->
exact information needed by LIFT-FM
```

This note records the hard constraints so new experiments do not accidentally
reintroduce an approximation while calling it exact.

## 21 essential input bits

For one 20-MS/s output the exact oracle depends on:

```text
previous Phase5   5 bits
middle raw Q4/I4  8 bits
current raw Q4/I4 8 bits
                  -------
                  21 bits
```

`tools/lift_fm_raw_wall.py` exhaustively finds a witness for every one of
those 21 bits: flipping that bit while holding the other inputs fixed can
change the exact DAC output.

That gives a useful lower bound. A pure serial LUT16 network has two 10-bit
addresses. Without side arithmetic, persistent state, instruction state, or
direct final mux logic, it can expose at most 20 independent original bits.
Therefore the remaining exact solution **must** exploit more of the
BitScrambler than two black-box LUTs.

This is why repeated token-training cannot make Trajectory v2 mathematically
exact.

## One raw sample per bundle

The useful escape is to stop treating the 50-ns pair as one indivisible input.

The BitScrambler can execute:

```text
bundle A: process/read one 8-bit raw sample
bundle B: process/read one 8-bit raw sample
```

while still producing one 20-MS/s result every two bundles.

That gives every 40-MS/s Q4 symbol its own LUT opportunity.

## LUT16 address/arithmetic overlap

For a 1024x16 LUT, address bits are `O16..O25`.

A raw-byte phase lookup only needs eight bits:

```text
O16..O23 = raw Q4/I4 byte
O24..O25 = 0
```

The same output word is also the source for the C5 counter high-half operation:

```text
counter-H operand = O24..O31
```

Therefore:

```text
O24..O25 = two fixed guard bits
O26..O31 = six free arithmetic bits
```

So a bundle can **simultaneously**:

- address the next exact raw-Q4 lookup; and
- feed up to six bits of arithmetic/state into `LDCTI*H` / `ADDCTI*H`.

Five free bits are enough to carry an exact Phase5 state. This is the strongest
known route for overlapping raw decode with the arithmetic backend instead of
spending a separate bundle on each.

## Why LUT8 is not automatically better

A 2048x8 LUT gives an 11-bit address, but only an 8-bit result.

If the datapath wants both `phase` and `-phase` to be available by static
bit selection, the ten requested output bit functions collapse to **nine**
unique Boolean functions. Eight result bits are therefore insufficient without
additional arithmetic.

LUT16 has enough output width for that representation, while still leaving the
raw8/address guard arrangement above.

## Next hardware target

The next superoptimization target is no longer vague:

```text
every 25 ns:
    LUT16(raw_n) -> exact compact phase/arithmetic code
    AND counter-half update in the same bundle

every 50 ns:
    recover exact d0+d1
    map to P20/G2
    emit [D,D]
```

The search is allowed to use:

- counters A/B;
- comparator outputs;
- previous-output state;
- instruction state;
- the six free high-half operand bits during raw lookup;
- one `read 8` per bundle.

Any candidate still has to match the exhaustive exact oracle before it can be
called LIFT-FM.

## Phase6: the middle sample is one parity bit

There is a stronger exact representation than carrying both adjacent deltas.

Lift the Phase5 circle into a signed six-bit local phase state. For one pair
`p -> m -> c`, the exact sum is always in `[-32, 30]`, so six signed bits
are sufficient.

The low five bits telescope:

```text
S[4:0] = (c - p) mod 32
```

The middle sample only decides whether bit 5 of that endpoint must be toggled.

Define `cross(a,b)` as the principal-branch crossing for one adjacent
transition. It has an exact comparator form:

```text
a4=0, b4=1 : cross = (b_low4 >= a_low4)
a4=1, b4=0 : cross = (a_low4 >  b_low4)
otherwise   : cross = 0
```

Then the complete exact pair is:

```text
S[4:0] = (c - p) & 31
S[5]   = (c < p) XOR cross(p,m) XOR cross(m,c)
```

The sign of the `+/-32` correction does not need to be retained: modulo 64,
both corrections toggle exactly the same sixth bit.

Equivalently, maintain an unwrapped Phase6 state `U`:

```text
U.low5 <- current Phase5
U.bit5 <- U.bit5 XOR cross(previous,current)
```

Then `signed6(U_c - U_p)` is exactly
`wrap32(m-p)+wrap32(c-m)`.

`tools/lift_fm_phase6.py` checks every one of the 1024 Phase5 transitions
for both lift parities and all 32768 `p,m,c` triplets, including equality
with the existing P20/G2 exact DAC oracle.

This moves the hardware target again. We do not need two complete adjacent
delta values in persistent state. We need:

- one endpoint subtraction;
- one persistent lift bit;
- exact branch-cross information for each 25-ns transition.

That is a much smaller state machine and maps naturally onto the C5's byte
comparators/counter state.
