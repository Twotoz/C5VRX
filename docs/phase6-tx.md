# Phase6 TX experiment

This mode starts from the proven `main` receive path. PARLIO writes every raw
Q4/I4 sample to the 40 MB/s ring, so ARC and the menu continue to observe raw
IQ. A single TX BitScrambler decodes the middle and endpoint Phase5 symbols,
then computes one 50 ns DAC sample duplicated as `[D,D]`.

The phase candidate is `wrap32(m-p) + wrap32(c-m)`, with **no second wrap**.
It is selected only when both 25 ns increments have magnitude at most 12 bins,
their difference is at most 6 bins, and their sum has magnitude at least 16.
Otherwise the mode uses Golden's `wrap32(c-p)` delta. This recovers a coherent
0° → 110° → 220° motion while suppressing false winding on ordinary, noisy
small-endpoint motion. As with any sampled FM discriminator, a true increment
of 180° or more within 25 ns cannot be unwrapped unambiguously.

The shared 1024×16 LUT packs the raw Phase5 decode (5 bits), a 25-state
factorization token (5 bits), and the DAC result (6 bits). The steady-state TX
schedule is four bundles per 50 ns pair; it replaces PR66's two TX bundles plus
two one-byte RX preprocessing bundles. No CPU participates in the sample path.
The exact instruction schedule is tested against 2,046 raw-IQ pairs, and the
LUT policy is checked exhaustively for all 32³ Phase5 triplets. On-device
throughput, image quality, and weak-signal benefit still require measurement.

The mode is opt-in via the VIDEO OUTPUT menu or serial `J`. Serial `J` toggles
between Phase6 TX and Golden with a reboot. Holding BOOT for three seconds
outside the menu restores Golden and ARC if serial is unavailable.
