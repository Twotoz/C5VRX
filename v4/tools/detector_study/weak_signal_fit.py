"""Offline physical-DAC table fitting for C5VRX by Twotoz and contributors.

Fixed encoder only; bounded floating least squares followed by nearest actual
DAC level. Neither integer filtered optimality nor global optimality is claimed.
"""
import numpy as np
from scipy import signal as sg
from scipy.optimize import minimize
from scipy.sparse.linalg import LinearOperator, lsmr
import weak_signal_sweep as W

B, D, P = W.B, W.D, W.P
LEVELS = B.DAC_VOLTS * 63 / B.DAC_VOLTS[-1]


def nearest(values):
    return np.argmin(abs(np.asarray(values)[..., None] - LEVELS), axis=-1)


def load_reference(name):
    import json
    m = json.loads((P.ROOT / 'tools' / (name.lower() + '_codebook.json')).read_text())
    m.update(name=name, current_tokens=56, previous_shift=1, middle_bits=[])
    return m


def case(seed, kind, cnr, rms, n=32768, cfo=1e6, dev=6.7e6, dc=0j, skew=1):
    sig, noise, endpoint, ire = P.stream(seed, n=n, kind=kind, cfo=cfo, deviation=dev)
    def raw(z):
        return D.raw_bytes(z.real*skew + 1j*z.imag + dc).astype(np.uint8)
    r = raw(W.iq_at(sig, noise, cnr, rms))
    clean = raw(W.iq_at(sig, np.zeros_like(noise), cnr, rms))
    truth = D.goggle(ire)
    cal = W.calibrate(W.decode(clean, load_reference('OVP56')), truth)
    lag, gain, offset = cal
    # All candidates share this OVP56 noiseless calibration. No candidate can
    # obtain a better score by changing its own clean scale/offset or delay.
    target = (np.roll(truth, lag)-offset)/gain * 63/B.DAC_VOLTS[-1]
    case_id = f'{seed}/{kind}/{cnr}/{rms}/{cfo}/{dev}/{dc.real}/{dc.imag}/{skew}'
    return dict(case_id=case_id, seed=seed, kind=kind, cnr=cnr, rms=rms, cfo=cfo, dev=dev,
                dc_real=dc.real, dc_imag=dc.imag, skew=skew, raw=r, clean=clean, truth=truth,
                calibration=cal, target=target,
                endpoint=np.interp(np.clip(endpoint, 0, 63), np.arange(64), LEVELS))


def dataset(seeds, cnrs, rms_values, n=32768, stress=False):
    result = []
    scenarios = [('random', 1e6, 6.7e6, 0j, 1),
                 ('bars', .5e6, 6.7e6, 0j, 1),
                 ('multitone', 1.5e6, 7.5e6, 0j, 1)]
    if stress:
        scenarios = [('random', .25e6, 5.5e6, .2-.3j, 1.1),
                     ('bars', 2e6, 8e6, -.25+.15j, .9),
                     ('multitone', .7e6, 7e6, .1+.1j, 1.05)]
    for seed in seeds:
        for kind, cfo, dev, dc, skew in scenarios:
            for rms in rms_values:
                for cnr in cnrs:
                    result.append(case(seed, kind, cnr, rms, n, cfo, dev, dc, skew))
    return result


def clean_constraints(model, reference, radii=None, cfos=None, ires=None):
    radii = radii or (1.25, 1.5, 1.75, 2, 2.25, 2.5, 3, 3.5, 4, 4.5, 5, 5.5)
    cfos = cfos or (.25e6, .5e6, .75e6, 1e6, 1.25e6, 1.5e6, 1.75e6, 2e6)
    ires = ires or (-40, -20, 0, 20, 40, 60, 80, 100)
    k = model['previous_tokens']*model['current_tokens']
    C, levels = [], []
    ref = LEVELS[np.array(reference['map']).ravel()]
    t = np.arange(8000)/40e6
    for radius in radii:
        for cfo in cfos:
            for ire in ires:
                raw = D.raw_bytes(radius*np.exp(2j*np.pi*(cfo+(ire-30)*6.7e6/140)*t)).astype(np.uint8)
                ix = B.indices(raw, model)[500:]
                C.append(np.bincount(ix, minlength=k)/len(ix))
                levels.append(np.mean(ref[B.indices(raw, reference)[500:]]))
    return np.array(C), np.array(levels)


def video_operator(model, cases, regularization, level_weight, C):
    k = model['previous_tokens']*model['current_tokens']
    indices = [B.indices(c['raw'], model) for c in cases]
    weights = [np.sqrt(3 if c['cnr'] in (2, 4, 6) else 1) for c in cases]
    counts = sum(w*w*np.bincount(ix, minlength=k) for ix, w in zip(indices, weights))
    norm = np.sqrt(np.maximum(counts, 1))
    sizes = [2*len(ix)-6000 for ix in indices]
    total = sum(sizes)
    cw = np.sqrt(total*level_weight/len(C))
    rootreg = np.sqrt(regularization)

    def forward(z):
        x = z/norm
        video = [w*D.goggle(np.repeat(x[ix], 2))[3000:-3000]
                 for ix, w in zip(indices, weights)]
        return np.concatenate(video + [rootreg*z, cw*(C@x)])

    def reverse(y):
        acc = np.zeros(k)
        offset = 0
        for ix, w, size in zip(indices, weights, sizes):
            yy = np.zeros(2*len(ix))
            yy[3000:-3000] = w*y[offset:offset+size]
            offset += size
            yy = sg.lfilter(*D.GOG, yy[::-1])[::-1].reshape(-1, 2).sum(1)
            acc += np.bincount(ix, weights=yy, minlength=k)
        return acc/norm + rootreg*y[total:total+k] + cw*(C.T@y[total+k:])/norm

    op = LinearOperator((total+k+len(C), k), matvec=forward, rmatvec=reverse)
    return op, norm, total, cw, weights


def refine(model, cases, regularization=.1, level_weight=100):
    base = LEVELS[np.array(model['map']).ravel()]
    C, levels = clean_constraints(model, load_reference('OVP56'))
    op, norm, total, cw, weights = video_operator(model, cases, regularization, level_weight, C)
    target = np.concatenate([w*c['target'][:-2][3000:-3000] for c, w in zip(cases, weights)] +
                            [np.zeros(len(base)), cw*levels])
    residual = target-op.matvec(base*norm)
    residual[total:total+len(base)] = 0  # Regularize changes, not absolute DAC voltage.
    solution = lsmr(op, residual, atol=2e-5, btol=2e-5, maxiter=60)
    lo, hi = -base*norm, (63-base)*norm

    def fg(z):
        error = op.matvec(z)-residual
        return .5*np.dot(error, error), op.rmatvec(error)

    opt = minimize(fg, np.clip(solution[0], lo, hi), jac=True, method='L-BFGS-B',
                   bounds=list(zip(lo, hi)),
                   options=dict(maxiter=150, ftol=1e-10, gtol=1e-5, maxls=30))
    values = nearest(base+opt.x/norm)
    m = dict(model, map=values.reshape(np.shape(model['map'])).tolist())
    m['fit'] = dict(regularization=regularization, level_weight=level_weight,
                    bounded_converged=bool(opt.success), bounded_iterations=int(opt.nit),
                    lsmr_iterations=int(solution[2]),
                    max_level_error=float(np.max(abs(C@LEVELS[values]-levels))))
    return m
