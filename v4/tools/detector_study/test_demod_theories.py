"""Known-tone, zero-input, calibration and deterministic family coverage checks."""
import numpy as np
import demod_theories as T
import mega_demod_bench as M


def main():
    raw = np.arange(256, dtype=np.uint8)
    i, q = T.F.D.cells(raw)
    assert i.min() == q.min() == -8 and i.max() == q.max() == 7
    assert np.array_equal(i, ((raw.astype(int) >> 4)+8)%16-8)
    assert np.array_equal(q, (raw.astype(int)+8)%16-8)
    for dtype in (np.uint8, np.int16, np.int64):
        a, b = T.F.D.cells(raw.astype(dtype))
        assert np.array_equal(a, i) and np.array_equal(b, q)
    n = 8192
    omega = 2*np.pi*2.1e6/40e6
    z = 3*np.exp(1j*(np.arange(n)*omega+.7))
    params = [[1, 1, 0, 0, 0, 0], [0, .001, 1, 0, 0, 0], [5, 1, 1, 1, 0, 0],
              [5, 1, 0, 0, 0, 0], [.6, .1, 0, np.pi, 1, 0], [.1, .1, 0, np.pi, 0, 0],
              [9, 1, 1, 0, 0, 0], [5, 1, .5, 1, 0, 0]]
    for family, p in zip(T.FAMILIES, params):
        m = dict(family=family, params=p)
        y = T.estimate(z, m)
        assert abs(np.mean(y[1000:])-omega) < .001, (family, np.mean(y[1000:]), omega)
        assert np.all(np.isfinite(T.estimate(np.zeros(n, complex), m))), family
        encoded = T.F.D.raw_bytes(z).astype(np.uint8)
        assert np.array_equal(T.decode(encoded, m), T.decode(encoded.astype(int), m)), family
    a, b = list(T.configurations(1000)), list(T.configurations(1000))
    assert a == b and {m['family'] for m in a} == set(T.FAMILIES)
    assert len({(m['family'], tuple(m['params'])) for m in a}) == 1000
    truth = 30+40*np.sin(np.arange(n)/19)
    y = np.roll((truth-7)/3, 9)
    cal = M.clean_calibration(y, truth, 128)
    assert M.metrics(y, truth, cal, 128)['sinad'] > 200
    assert abs(M.metrics(.1*y, truth, cal, 128)['contrast_percent']-10) < 1e-6
    rng = np.random.default_rng(2021)
    noise = rng.normal(size=n)
    assert M.metrics(y+noise, truth, cal, 128)['sinad'] < M.metrics(y, truth, cal, 128)['sinad']
    # Improved contrast toward 100% is allowed, but additional overshoot is not.
    rows = [dict(model=name, seed=1, kind='random', rms=3, cnr=cnr, sinad=10,
                 clicks=0, contrast_percent=contrast) for name, contrast in
            [('OVP56', 95), ('better', 98), ('overshoot', 110)] for cnr in (2, 14)]
    stats = M.summarize(rows, strong_screen=True)
    assert stats['better']['strong_ok'] and not stats['overshoot']['strong_ok']
    # Same seed/kind/CNR/RMS under two channel conditions must never compare
    # against the other condition's reference. Baseline must equal itself.
    mixed = []
    for condition, sinad in [('nominal', 14), ('stress', 5)]:
        for name in ('OVP56', 'candidate'):
            for cnr in (2, 14):
                mixed.append(dict(model=name, case_id=f'{condition}/{cnr}', seed=1,
                                  kind='random', rms=3, cnr=cnr,
                                  sinad=sinad-(.1 if name == 'candidate' else 0),
                                  clicks=0, contrast_percent=100))
    stats = M.summarize(mixed, strong_screen=True)
    assert stats['OVP56']['strong_ok'] and stats['OVP56']['max_strong_sinad_loss'] == 0
    assert abs(stats['candidate']['max_strong_sinad_loss']-.1) < 1e-8
    for r in mixed:
        del r['case_id']
    try:
        M.summarize(mixed)
    except ValueError:
        pass
    else:
        raise AssertionError('ambiguous mixed scenarios must be rejected')
    print('PASS eight theory known tones, zero-input stability, deterministic unique configurations, clean delay/level calibration and noisy contrast loss')


if __name__ == '__main__':
    main()
