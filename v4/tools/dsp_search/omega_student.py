"""C5VRX: jointly learned observation encoder/recurrent belief transducer.

Distillation uses only the causal teacher posterior, never future IQ/truth.
Free-form clusters replace phase/frequency grids. DAgger-style closed-loop
updates address student state-distribution drift. Every iteration updates
transitions AND DAC AND observation assignments, retaining shared LUT packing.
"""
import numpy as np
from numba import njit
import compile_overlay as C


def addresses(raw, layout):
    words = raw[0:len(raw)//2*2:2].astype(np.int64) | (raw[1:len(raw)//2*2:2].astype(np.int64) << 8)
    a = np.zeros(len(words), np.int64)
    for j, bit in enumerate(C.pair_bits(layout)): a |= ((words >> bit) & 1) << j
    return a


@njit(cache=True)
def trace(a, enc, transition):
    old = np.zeros(len(a), np.int64); state = 0
    for k in range(len(a)):
        old[k] = state; state = transition[state, enc[a[k]]]
    return old


def fit(sequences, bits=3, layout='4411', seed=1, rounds=5, sync_weight=0., feature_scale=None, feature_prior=None):
    from sklearn.cluster import MiniBatchKMeans
    import omega_bayes as B
    T = 1 << bits; S = 1024 // T
    x = np.concatenate([c['features'] for c in sequences]); hz = np.concatenate([c['hz'] for c in sequences])
    # Task-weighted posterior distillation: preserve rare low-frequency
    # plateaus using teacher evidence, never a true raster label. This is a
    # weighted Bayes-risk objective, not a runtime amplitude multiplier.
    w = 1 + sync_weight * np.clip((-hz - .7e6) / 1.2e6, 0, 1) ** 2
    addr = [addresses(c['raw'], layout) for c in sequences]
    scale = np.array([1, 1, 2, 2, .5, .5, .5, .5, .5, .5, .5, 2, 1, 1.] if feature_scale is None else feature_scale)
    if scale.shape != (x.shape[1],): raise ValueError('feature scale dimension')
    cluster = MiniBatchKMeans(n_clusters=S, random_state=seed, batch_size=4096, n_init=3)
    # State zero is the closest learned belief to a diffuse startup prior.
    labels = cluster.fit_predict(x * scale, sample_weight=w); centres = cluster.cluster_centers_
    prior = np.array([0, 0, 0, 0, 1/3, 1/3, 1/3, .25, .25, .25, .25, 1/12, .5, .3] if feature_prior is None else feature_prior) * scale
    start = np.argmin(np.sum((centres - prior) ** 2, 1)); centres[[0, start]] = centres[[start, 0]]
    labels = cluster.predict(x * scale)
    a = np.concatenate(addr); xx = x * scale
    occupancy = np.bincount(a, weights=w, minlength=1024)
    # Encoder initially clusters conditional predictive posterior features.
    address_mean = np.stack([np.bincount(a, weights=w * xx[:, j], minlength=1024) for j in range(xx.shape[1])], 1)
    address_mean /= np.maximum(occupancy, 1)[:, None]
    encoder = MiniBatchKMeans(n_clusters=T, random_state=seed+1, n_init=3, batch_size=1024).fit_predict(address_mean)
    transition = np.zeros((S, T), np.int64); dac = np.full((S, T), B.codes(np.array([1e6]))[0], np.int64)
    teacher_old = []; offset = 0
    for c in sequences:
        ll = labels[offset:offset+len(c['hz'])]; teacher_old.append(np.r_[0, ll[:-1]]); offset += len(ll)
    old = np.concatenate(teacher_old); history = []
    for iteration in range(rounds):
        tok = encoder[a]; index = old * T + tok
        count = np.bincount(index, weights=w, minlength=1024)
        sums = np.stack([np.bincount(index, weights=w * xx[:, j], minlength=1024) for j in range(xx.shape[1])], 1)
        avg = sums / np.maximum(count, 1)[:, None]
        distance = np.sum((avg[:, None, :] - centres[None, :, :]) ** 2, 2)
        nxt = np.argmin(distance, 1)
        # Unvisited words retain their former policy, no accidental zero reset.
        transition.ravel()[count > 0] = nxt[count > 0]
        meanhz = np.bincount(index, weights=w * hz, minlength=1024) / np.maximum(count, 1)
        dac.ravel()[count > 0] = B.codes(meanhz[count > 0])
        # Joint token assignment minimizes belief projection + DAC risk over
        # the CURRENT recurrent occupancy, rather than clustering raw angles.
        costs = np.zeros((1024, T))
        for t in range(T):
            prediction = centres[transition[old, t]]
            risk = np.sum((prediction - xx) ** 2, 1)
            risk += .2 * ((dac[old, t] - B.codes(hz)) / 63.) ** 2
            costs[:, t] = np.bincount(a, weights=w * risk, minlength=1024)
        encoder[occupancy > 0] = np.argmin(costs[occupancy > 0], 1)
        old = np.concatenate([trace(aa, encoder, transition) for aa in addr])
        error = float(np.mean(np.sum((centres[transition[old, encoder[a]]] - xx) ** 2, 1)))
        history.append(dict(iteration=iteration, posterior_distortion=error, visited_words=int(np.count_nonzero(count))))
    lut = (dac.ravel().astype(np.uint16) | (transition.ravel().astype(np.uint16) << 6))
    lut |= encoder.astype(np.uint16) << (16-bits)
    model = dict(name='OMEGA BELIEF', params=dict(token_bits=bits, pair_layout=layout,
                 belief_fsm=True, phases=4, confidence_groups=1, seed=seed, sync_weight=sync_weight), lut=lut.astype(int).tolist())
    C.build(model)
    return model, dict(rounds=history, states=S, observations=T, layout=layout, seed=seed,
                       centres=centres.tolist(), encoder=encoder.tolist(), sync_weight=sync_weight)
