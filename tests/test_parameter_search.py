import unittest

from app.parameter_search import (
    factor_integer,
    is_prime,
    ntt_prime_candidates,
    recommend_rlwe,
    security_margin_bits,
    parse_request,
    sparse_ternary_spec,
    ring_dimensions,
)


class ParameterSearchTests(unittest.TestCase):
    def test_prime_and_factor_helpers(self):
        self.assertTrue(is_prime(12289))
        self.assertFalse(is_prime(12291))
        self.assertEqual(factor_integer(12288), {2: 12, 3: 1})

    def test_ntt_candidates_satisfy_split_condition(self):
        primes = ntt_prime_candidates(1024, 24, limit=5)
        self.assertTrue(primes)
        for q in primes:
            self.assertEqual((q - 1) % 1024, 0)
            self.assertTrue(is_prime(q))

    def test_ntt_scale_half_n_allows_smaller_modulus(self):
        primes = ntt_prime_candidates(512, 24, ntt_scale_power=1, limit=5)
        self.assertIn(257, primes)
        self.assertIn(769, primes)
        for q in primes:
            self.assertEqual((q - 1) % 256, 0)
            self.assertTrue(is_prime(q))

    def test_default_recommendation_has_rlwe_shape(self):
        result = recommend_rlwe({"targetSecurity": 128, "securityModel": "min"})
        candidate = result["recommendation"]
        n = candidate["ring"]["n"]
        q = candidate["modulus"]["q"]
        self.assertGreaterEqual(candidate["selection"]["selected_security_bits"], 128)
        self.assertEqual((q - 1) % n, 0)
        self.assertIn(candidate["distribution"]["family"], {"centered_binomial", "sparse_ternary"})

    def test_sparse_ternary_probability_model(self):
        spec = sparse_ternary_spec(n=512, l0=2, l1=1)
        self.assertEqual(spec.family, "sparse_ternary")
        self.assertAlmostEqual(spec.parameters["probability_plus"], 3 / 32)
        self.assertAlmostEqual(spec.parameters["probability_minus"], 3 / 32)
        self.assertAlmostEqual(spec.variance, 3 / 16)
        self.assertEqual(spec.estimator["plus_weight"], 48)
        self.assertEqual(spec.estimator["minus_weight"], 48)

    def test_ternary_ring_candidates(self):
        dims = ring_dimensions("ternary")
        self.assertIn(384, dims)
        self.assertIn(768, dims)
        self.assertTrue(all(n % 2 == 0 for n in dims))

        result = recommend_rlwe({
            "ringFamily": "ternary",
            "targetSecurity": 128,
            "redCostModel": "matzov",
            "nttScalePower": 1,
            "maxQBits": 24,
        })
        candidate = result["recommendation"]
        self.assertEqual(candidate["ring"]["family_id"], "ternary")
        self.assertIn("- x^", candidate["ring"]["polynomial"])
        self.assertEqual((candidate["modulus"]["q"] - 1) % (3 * candidate["ring"]["n"] // 2), 0)
        self.assertIn("3", candidate["modulus"]["q_minus_1_factorization"])

    def test_matzov_128_recommendations_are_tight_lower_bound(self):
        request = {"targetSecurity": 128, "securityModel": "matzov", "maxQBits": 24}
        result = recommend_rlwe(request)
        options = [result["recommendation"], *result["alternatives"][:2]]
        parsed = parse_request(request)
        margins = [security_margin_bits(option["security"], parsed) for option in options]
        moduli = [option["modulus"]["q"] for option in options]

        self.assertEqual(len(options), 3)
        self.assertTrue(all(margin >= 0 for margin in margins))
        self.assertEqual(moduli, sorted(set(moduli)))
        self.assertTrue(all(option["modulus"]["ntt_layers_remaining"] in (0, 1) for option in options))


if __name__ == "__main__":
    unittest.main()
