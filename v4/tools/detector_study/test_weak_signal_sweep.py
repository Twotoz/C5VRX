"""Check power control and detect scoring that conceals weak-signal collapse."""
import numpy as np
import weak_signal_sweep as W


def main():
    # Orthogonal unit-power vectors isolate expected-power normalization exactly.
    phase = np.linspace(0, 2*np.pi, 20000, endpoint=False)
    sig, noise = np.exp(1j*phase), np.exp(2j*phase)
    for cnr in W.CNRS:
        z = W.iq_at(sig, noise, cnr, 3)
        assert abs(np.mean(abs(z)**2) - 9) < 1e-10
        clean = W.iq_at(sig, np.zeros_like(noise), cnr, 3)
        assert abs(np.mean(abs(clean)**2) / np.mean(abs(z-clean)**2) - 10**(cnr/10)) < 1e-10
    truth = 30 + 50*np.sin(phase)
    y = (truth-12)/7
    cal = W.calibrate(y, truth)
    perfect, clicks = W.score(y, truth, cal)
    assert perfect > 200 and clicks == 0
    assert abs(W.contrast(y, truth, cal)-100) < 1e-8
    # Re-fitting this diminished output could hide its absolute amplitude loss.
    weak, clicks = W.score(y*.1, truth, cal)
    assert weak < 2 and clicks > 0
    assert abs(W.contrast(y*.1, truth, cal)-10) < 1e-8
    assert W.score(y+10, truth, cal)[0] < 0  # DC error also counts.
    assert W.occupancy(np.full(10000, 0, dtype=np.uint8))['origin_permille'] == 1000
    assert W.occupancy(np.full(10000, 0x77, dtype=np.uint8))['rail_permille'] == 1000
    print('PASS fixed total IQ power, known C/N, frozen clean calibration, level/DC collapse and occupancy')


if __name__ == '__main__':
    main()
