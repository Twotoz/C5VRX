"""C5VRX by Twotoz/contributors: causal folded-IQ Bayesian FM reference.

No future samples, true C/N, video truth, raster clock or VTX deviation enter
filter(). The likelihood is exact for iq_lanes.quantize plus independent
Gaussian noise, NOT an exact model of the undocumented physical C5 ADC.
A persistent latent noise class is marginalized, never selected by test C/N.
"""
import numpy as np
from scipy.special import ndtr
from numba import njit


def component_likelihood(mean, sigma, lane='ultrafine'):
    shift = {'coarse': 6, 'fine': 5, 'ultrafine': 4}[lane]
    adc = np.arange(-512, 512)
    lo = adc / (1 << shift); hi = (adc + 1) / (1 << shift)
    lo[0] = -np.inf; hi[-1] = np.inf
    codes = (((adc & 1023) >> 9) << 3) | (((adc & 1023) >> shift) & 7)
    mass = ndtr((hi[None, :] - mean[:, None]) / sigma) - ndtr((lo[None, :] - mean[:, None]) / sigma)
    return np.stack([mass[:, codes == c].sum(1) for c in range(16)], 1)


def setup(phases=32, frequencies=32, rms=3., lane='ultrafine', jump=.12, contamination=.015):
    # Three persistent hypotheses for normalized total IQ power. These are
    # priors, NOT supplied C/N. Wide transitions admit real chroma and edges.
    noise_db = np.array([1., 8., 23.]); ratio = 10 ** (noise_db / 10)
    angle = 2 * np.pi * np.arange(phases) / phases
    freq = np.linspace(-5.5e6, 6.5e6, frequencies)
    emission = np.empty((3, phases, 256))
    for r, rho in enumerate(ratio):
        A = rms * np.sqrt(rho / (1 + rho)); sigma = rms / np.sqrt(2 * (1 + rho))
        pi = component_likelihood(A * np.cos(angle), sigma, lane)
        pq = component_likelihood(A * np.sin(angle), sigma, lane)
        emission[r] = (pi[:, :, None] * pq[:, None, :]).reshape(phases, 256)
    emission = (1 - contamination) * emission + contamination / 256
    df = freq[:, None] - freq[None, :]
    narrow = np.exp(-.5 * (df / .35e6) ** 2); narrow /= narrow.sum(0)
    wide = np.exp(-.5 * (df / 2.4e6) ** 2); wide /= wide.sum(0)
    trans = (1 - jump) * narrow + jump * wide
    shift = freq * phases / 40e6
    lower = np.floor(shift).astype(np.int64); fraction = shift - lower
    return dict(emission=emission, transition=trans, lower=lower, fraction=fraction,
                freq=freq, phases=phases, frequencies=frequencies, lane=lane,
                rms=rms, jump=jump, contamination=contamination)


@njit(cache=True)
def _filter(raw, em, trans, lower, fraction, freq, stride):
    R, P, _ = em.shape; F = len(freq)
    belief = np.full((R, F, P), 1. / (R * F * P))
    features = np.zeros((len(raw) // stride, 14)); output = np.zeros(len(raw) // stride)
    phase_cos = np.cos(2 * np.pi * np.arange(P) / P); phase_sin = np.sin(2 * np.pi * np.arange(P) / P)
    for n in range(len(raw)):
        # Sum-product forward recursion, not a MAP path or phase-detector loop.
        predictive = np.zeros_like(belief)
        for r in range(R):
            predictive[r] = trans @ belief[r]
        norm = 0.; updated = np.empty_like(belief)
        for r in range(R):
            for f in range(F):
                for p in range(P):
                    a = (p - lower[f]) % P; b = (a - 1) % P
                    # Small phase diffusion prevents permanent grid hangup.
                    v = (1 - fraction[f]) * predictive[r, f, a] + fraction[f] * predictive[r, f, b]
                    v = .98 * v + .01 * (predictive[r, f, (a - 1) % P] + predictive[r, f, (a + 1) % P])
                    # A persistent inferred regime with 0.2% prior migration.
                    other = 0.
                    for rr in range(R): other += predictive[rr, f, a]
                    v = (.998 * v + .002 * other / R) * em[r, p, raw[n]]
                    updated[r, f, p] = v; norm += v
        belief = updated / max(norm, 1e-300)
        if n % stride == stride - 1:
            k = n // stride; mean = 0.; sq = 0.; entropy = 0.
            for r in range(R):
                for f in range(F):
                    x = freq[f] / 6e6
                    for p in range(P):
                        v = belief[r, f, p]; mean += v * x; sq += v * x * x
                        features[k, 0] += v * phase_cos[p]; features[k, 1] += v * phase_sin[p]
                        features[k, 2] += v * x * phase_cos[p]; features[k, 3] += v * x * phase_sin[p]
                        features[k, 4 + r] += v
                        features[k, 7 + min(3, f * 4 // F)] += v
                        features[k, 13] += v * (freq[f] < -1.8e6)
            features[k, 11] = mean; features[k, 12] = np.sqrt(max(0., sq - mean * mean))
            output[k] = mean * 6e6
    return output, features


def filter(raw, cfg, stride=2):
    if stride not in (1,2): raise ValueError("only causal 25/50-ns outputs")
    return _filter(np.asarray(raw, np.uint8), cfg['emission'], cfg['transition'], cfg['lower'], cfg['fraction'], cfg['freq'], stride)


def codes(hz):
    import hardware as H
    return np.rint(np.clip((hz * 256 / 20e6 - H.B.P.OFFSET) * H.B.P.SCALE, 0, 63)).astype(np.uint8)


def decode(raw, cfg, dev=1., centre=1e6):
    import hardware as H
    hz, features = filter(raw, cfg)
    out = codes(1e6 + (hz - centre) / dev)
    y = np.zeros(len(raw)); y[2:] = np.repeat(H.B.DAC_VOLTS[out], 2)[:len(raw) - 2]
    return H.B.D.goggle(y), features, hz
