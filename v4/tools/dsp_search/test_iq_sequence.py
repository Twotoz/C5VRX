"""Physical-model, folded-likelihood and observation-deadline checks."""
import unittest

import numpy as np
from scipy import signal

import iq_sequence as Q
from iq_lanes import quantize
from omega_bayes import component_likelihood


class SequenceTest(unittest.TestCase):
    def test_interval_probabilities_match_existing_adc_integral(self):
        for lane in Q.SHIFTS:
            lo, hi, counts = Q.cell_intervals(lane)
            mu = np.array([-40., -10., -3.2, .5, 7.1, 10., 40.])
            expected = component_likelihood(mu, .6, lane)
            actual = np.array([[Q.cell_moments(c, m, .36, lo, hi, counts)[0]
                                for c in range(16)] for m in mu])
            np.testing.assert_allclose(actual, expected, atol=2e-14)
            np.testing.assert_allclose(actual.sum(1), 1., atol=2e-14)

    def test_folded_conditional_moments_match_sampled_adc_including_rails(self):
        rng = np.random.default_rng(2300201)
        for lane in ('coarse', 'ultrafine'):
            lo, hi, counts = Q.cell_intervals(lane)
            for mean, sigma in ((7.5, 2.), (31.8, 1.), (-31.8, 1.)):
                samples = mean + sigma * rng.normal(size=200000)
                observed = quantize(samples.astype(complex), lane) >> 4
                for code in range(16):
                    selected = samples[observed == code]
                    p, m, v = Q.cell_moments(code, mean, sigma**2, lo, hi, counts)
                    if len(selected) > 2000:
                        self.assertLess(abs(len(selected) / len(samples) - p), .006)
                        mean_se = np.sqrt(v / len(selected))
                        variance_se = np.sqrt(np.var((selected - m)**2) / len(selected))
                        self.assertLess(abs(selected.mean() - m), 5 * mean_se + .002)
                        self.assertLess(abs(selected.var() - v), 5 * variance_se + .002)

    def test_receiver_noise_correlation_matches_independent_white_noise(self):
        b, a, rho = Q.receiver_model()
        noise = np.random.default_rng(2300202).normal(size=400000)
        filtered = signal.lfilter(b, a, noise)[2000::2]
        measured = np.corrcoef(filtered[:-1], filtered[1:])[0, 1]
        self.assertLess(abs(measured - rho), .008)
        self.assertGreater(rho, 0.)

    def test_future_samples_cannot_change_emitted_estimates(self):
        raw = np.random.default_rng(2300203).integers(0, 256, 2048, dtype=np.uint8)
        for lag in (0, 7, 16):
            cfg = Q.setup(particles=128, lag=lag)
            full = Q.filter(raw, cfg)
            prefix = Q.filter(raw[:1024], cfg)
            emitted = prefix.available_samples < 1024
            np.testing.assert_array_equal(full.hz[:len(prefix.hz)][emitted], prefix.hz[emitted])
            np.testing.assert_array_equal(full.diagnostics[:len(prefix.hz)][emitted], prefix.diagnostics[emitted])
            self.assertTrue(np.isnan(prefix.hz[~emitted]).all())
            np.testing.assert_array_equal(prefix.diagnostics[emitted, 5], prefix.available_samples[emitted])

    def test_filtered_cw_unknown_phase_gain_and_frequency(self):
        cfg = Q.setup(particles=1024)
        b, a, _ = Q.receiver_model()
        for amp, freq in ((3., -2e6), (10., 1e6)):
            t = np.arange(16384) / 80e6
            z = signal.lfilter(b, a, amp * np.exp(1j * (.73 + 2 * np.pi * freq * t)))[::2]
            result = Q.filter(quantize(z, 'ultrafine'), cfg)
            self.assertLess(abs(result.hz[1024:].mean() - freq), 250000.)
            self.assertTrue(np.isfinite(result.diagnostics).all())

    def test_noise_only_keeps_finite_uncertainty(self):
        rng = np.random.default_rng(2300204)
        raw = quantize(rng.normal(size=2048) + 1j * rng.normal(size=2048), 'ultrafine')
        result = Q.filter(raw, Q.setup(particles=256))
        self.assertTrue(np.isfinite(result.hz).all())
        self.assertTrue(np.isfinite(result.diagnostics).all())
        self.assertGreater(result.diagnostics[256:, 0].mean(), 100000.)

    def test_pair4411_never_reads_discarded_second_sample_bits(self):
        rng = np.random.default_rng(2300205)
        raw = rng.integers(0, 256, 2048, dtype=np.uint8)
        changed = raw.copy()
        changed[1::2] = (raw[1::2] & 0x88) | (rng.integers(0, 256, 1024, dtype=np.uint8) & 0x77)
        cfg = Q.setup(particles=128, observations='pair4411')
        a, b = Q.filter(raw, cfg), Q.filter(changed, cfg)
        np.testing.assert_array_equal(a.hz, b.hz)
        np.testing.assert_array_equal(a.diagnostics, b.diagnostics)

    def test_delayed_dac_has_no_wrap_or_fabricated_prefix(self):
        raw = quantize(3 * np.exp(2j * np.pi * 1e6 * np.arange(2048) / 40e6), 'ultrafine')
        y, estimate = Q.decode(raw, Q.setup(particles=128, lag=16))
        self.assertTrue(np.isnan(y[:18]).all())
        self.assertTrue(np.isfinite(y[18:]).all())
        self.assertTrue(np.isnan(estimate.hz[-8:]).all())

    def test_bad_inputs_are_refused(self):
        cfg = Q.setup(particles=128)
        for raw in (np.zeros(1, np.uint8), np.zeros(3, np.uint8),
                    np.zeros(10), np.zeros((2, 2), np.uint8)):
            with self.assertRaises(ValueError):
                Q.filter(raw, cfg)
        for kwargs in ({'lag': -1}, {'lag': 321}, {'particles': 4},
                       {'lane': 'unknown'}, {'cutoff_hz': 40e6}, {'temper': 0}):
            with self.assertRaises(ValueError):
                Q.setup(**kwargs)


if __name__ == '__main__':
    unittest.main()
