"""Independent discrete-DAC minima, filtered adjoint and promotion guard tests."""
import numpy as np
import weak_signal_search as S
import weak_signal_fit as F


def main():
    rng = np.random.default_rng(2011)
    m = dict(next(S.B.layouts()), encoder=(np.arange(256)%16).tolist(), name='test')
    w = rng.integers(1, 10, (1, 256, 256)).astype(float)
    y = rng.uniform(-20, 80, w.shape)
    m['map'] = S.fit_table(m, (w, w*y, w*y*y), 0)
    enc = np.array(m['encoder'])
    ix = ((enc[:, None] >> 1)*16+enc[None, :]).ravel()
    # Independent exhaustive search over all 64 actual resistor-DAC levels.
    for cell, code in enumerate(np.array(m['map']).ravel()):
        mask = ix == cell
        ww, yy = w.ravel()[mask], y.ravel()[mask]
        costs = np.array([np.sum(ww*(v-yy)**2) for v in F.LEVELS])
        assert costs[code] <= costs.min()+1e-7
    empty = S.fit_table(m, (w*0, w*0, w*0), 0)
    assert np.all(np.isfinite(empty)) and min(np.ravel(empty)) >= 0
    assert max(np.ravel(empty)) <= 63
    # Check the complete weighted finite-filter/level/regularization operator,
    # not merely its component IIR. Random vectors expose indexing/scaling bugs.
    cases = [dict(raw=rng.integers(0, 256, 8192, dtype=np.uint8), cnr=c) for c in (2, 14)]
    k = m['previous_tokens']*m['current_tokens']
    C = rng.uniform(size=(7, k)); C /= C.sum(1)[:, None]
    op, _, _, _, _ = F.video_operator(m, cases, .1, 100, C)
    x, z = rng.normal(size=k), rng.normal(size=op.shape[0])
    assert abs(np.dot(op@x, z)-np.dot(x, op.rmatvec(z))) < 1e-8
    # The common calibration must count, rather than fit away, contrast loss.
    t = np.linspace(0, 2*np.pi, 16384)
    truth = 30+50*np.sin(t)
    cal = F.W.calibrate(truth, truth)
    assert F.W.contrast(.5*truth, truth, cal) < 51
    # Strong-signal loss cannot be paid for by a higher weak average.
    rows = []
    for name in ('OVP56', 'candidate'):
        for cnr in (2, 14):
            rows.append(dict(model=name, seed=1, kind='random', rms=3, cnr=cnr,
                             sinad=5 if cnr == 2 else (14 if name == 'OVP56' else 13),
                             clicks=0, contrast_percent=100))
    summary = S.summarize(rows)
    assert summary['OVP56']['strong_ok'] and not summary['candidate']['strong_ok']
    S.B.source_check(m)
    print('PASS exhaustive physical-DAC bin minima, empty-bin fallback, full filtered adjoint, contrast and strong guards, compiled schedule')


if __name__ == '__main__':
    main()
