#!/usr/bin/env python3
"""Exact 6-bit lifted-phase identities for LIFT-FM.

The exact two-adjacent Phase5 pair sum lies in [-32, 30], so one signed
6-bit value is sufficient. This script proves that the middle sample only
contributes winding parity to bit 5; the low five bits telescope to c-p.

For Phase5 a,b in 0..31 define cross(a,b) as the event where the raw endpoint
difference must be shifted by +/-32 to enter the principal [-16,15] interval.

cross(a,b) is exactly:
  a4=0,b4=1 -> b_low4 >= a_low4
  a4=1,b4=0 -> a_low4 > b_low4
  otherwise -> false

For p,m,c:
  low5 = (c-p) mod 32
  bit5 = (c < p) XOR cross(p,m) XOR cross(m,c)

Interpreting that 6-bit word as two's complement yields exactly:
  wrap32(m-p) + wrap32(c-m)

All 32768 Phase5 triplets are exhaustively checked.
"""

from __future__ import annotations

from lift_fm_synth import oracle, pair_sum


def wrap32(delta: int) -> int:
    if delta > 15:
        delta -= 32
    elif delta < -16:
        delta += 32
    return delta


def signed6(code: int) -> int:
    code &= 0x3F
    return code - 64 if code & 0x20 else code


def crossing_direct(previous: int, current: int) -> int:
    raw = current - previous
    return int(raw > 15 or raw < -16)


def crossing_compare(previous: int, current: int) -> int:
    p4 = (previous >> 4) & 1
    c4 = (current >> 4) & 1
    p_lo = previous & 0x0F
    c_lo = current & 0x0F

    if p4 == 0 and c4 == 1:
        return int(c_lo >= p_lo)
    if p4 == 1 and c4 == 0:
        return int(p_lo > c_lo)
    return 0


def lifted_pair_code(previous: int, middle: int, current: int) -> int:
    low5 = (current - previous) & 0x1F
    bit5 = (
        int(current < previous)
        ^ crossing_compare(previous, middle)
        ^ crossing_compare(middle, current)
    )
    return low5 | (bit5 << 5)


def update_unwrapped_phase6(unwrapped: int, current_phase5: int) -> int:
    previous_phase5 = unwrapped & 0x1F
    parity = (unwrapped >> 5) & 1
    parity ^= crossing_compare(previous_phase5, current_phase5)
    return current_phase5 | (parity << 5)


def exhaustive_verify() -> None:
    transitions = 0
    for previous in range(32):
        for current in range(32):
            direct = crossing_direct(previous, current)
            compare = crossing_compare(previous, current)
            if direct != compare:
                raise AssertionError(
                    f"cross mismatch p={previous} c={current}: "
                    f"direct={direct} compare={compare}"
                )

            # Starting with either lift parity, one transition must equal an
            # exact principal Phase5 step in the lifted Z64 state.
            for parity in (0, 1):
                before = previous | (parity << 5)
                after = update_unwrapped_phase6(before, current)
                got = signed6((after - before) & 0x3F)
                want = wrap32(current - previous)
                if got != want:
                    raise AssertionError(
                        f"phase6 transition mismatch p={previous} c={current} "
                        f"parity={parity}: got={got} want={want}"
                    )
            transitions += 1

    triplets = 0
    for previous in range(32):
        for middle in range(32):
            for current in range(32):
                code = lifted_pair_code(previous, middle, current)
                got = signed6(code)
                want = pair_sum(previous, middle, current)
                if got != want:
                    raise AssertionError(
                        f"pair mismatch p={previous} m={middle} c={current}: "
                        f"code={code} got={got} want={want}"
                    )

                # The existing exact DAC oracle must agree with the signed
                # Phase6 value mapped through P20/G2.
                mapped = max(0, min(63, 20 + 6 * got))
                if mapped != oracle(previous, middle, current):
                    raise AssertionError(
                        f"DAC mismatch p={previous} m={middle} c={current}: "
                        f"mapped={mapped} oracle={oracle(previous,middle,current)}"
                    )

                # Equivalent recurrence proof: lift p, step through m and c,
                # then subtract the original lifted endpoint.
                u0 = previous
                u1 = update_unwrapped_phase6(u0, middle)
                u2 = update_unwrapped_phase6(u1, current)
                recurrent = signed6((u2 - u0) & 0x3F)
                if recurrent != want:
                    raise AssertionError(
                        f"recurrence mismatch p={previous} m={middle} c={current}"
                    )
                triplets += 1

    if transitions != 1024 or triplets != 32768:
        raise AssertionError("unexpected exhaustive state count")


def main() -> None:
    exhaustive_verify()
    print(
        "LIFT-FM Phase6 proof passed: "
        "1024/1024 transitions and 32768/32768 triplets exact; "
        "middle sample reduces to winding parity; "
        "pair sum is one signed 6-bit lifted endpoint"
    )


if __name__ == "__main__":
    main()
