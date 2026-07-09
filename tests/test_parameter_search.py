import unittest
from unittest.mock import patch

from app.config import AppConfig, EstimatorConfig
from app.parameter_search import (
    factor_integer,
    is_prime,
    ntt_prime_candidates,
    recommend_rlwe,
    run_sage_estimator,
    security_margin_bits,
    parse_request,
    sparse_ternary_spec,
    uniform_spec,
    lwr_rounding_profile,
    ring_dimensions,
)
from app.estimator_runner import estimator_distribution


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
        result = recommend_rlwe({"targetSecurity": 128, "securityModel": "classical"})
        candidate = result["recommendation"]
        n = candidate["ring"]["n"]
        q = candidate["modulus"]["q"]
        self.assertGreaterEqual(candidate["selection"]["selected_security_bits"], 128)
        self.assertEqual((q - 1) % n, 0)
        self.assertIn(candidate["distribution"]["family"], {"centered_binomial", "sparse_ternary"})
        self.assertIn("visual_scores", candidate)
        self.assertEqual(candidate["visual_scores"]["security"]["max_bits"], 512)
        self.assertGreater(candidate["visual_scores"]["compactness"]["score"], 0)
        self.assertEqual(candidate["visual_scores"]["performance"]["score"], 1.0)

    def test_ntt_unfriendly_mode_does_not_require_n_q_divisibility(self):
        primes = ntt_prime_candidates(512, 12, ntt_scale_power=6, min_q_bits=2, limit=8)
        self.assertEqual(primes[0], 3)
        self.assertTrue(any((q - 1) % 512 != 0 for q in primes))

        result = recommend_rlwe({
            "targetSecurity": 128,
            "securityModel": "classical",
            "nttScalePower": 6,
            "minQBits": 2,
            "maxQBits": 12,
            "useEstimator": False,
        })
        candidate = result["recommendation"]

        self.assertFalse(candidate["modulus"]["ntt_friendly"])
        self.assertEqual(candidate["modulus"]["ntt_condition"], "no restriction of n and q (NTT unfriendly)")
        self.assertEqual(candidate["visual_scores"]["performance"]["score"], 0.0)
        self.assertEqual(candidate["visual_scores"]["performance"]["k_label"], "lift")

    def test_sparse_ternary_probability_model(self):
        spec = sparse_ternary_spec(n=512, l0=2, l1=1)
        self.assertEqual(spec.family, "sparse_ternary")
        self.assertAlmostEqual(spec.parameters["probability_plus"], 3 / 32)
        self.assertAlmostEqual(spec.parameters["probability_minus"], 3 / 32)
        self.assertAlmostEqual(spec.variance, 3 / 16)
        self.assertEqual(spec.estimator["plus_weight"], 48)
        self.assertEqual(spec.estimator["minus_weight"], 48)

    def test_classical_auto_candidate_uses_sparse_ternary(self):
        spec = sparse_ternary_spec(n=512, l0=1, l1=0)
        self.assertAlmostEqual(spec.parameters["probability_plus"], 1 / 4)
        self.assertAlmostEqual(spec.parameters["probability_minus"], 1 / 4)
        self.assertAlmostEqual(spec.parameters["probability_zero"], 1 / 2)
        self.assertEqual(spec.estimator["fast_screen_penalty_bits"], 0.0)

        result = recommend_rlwe({
            "hardProblemCategory": "lwe",
            "hardProblemVariant": "rlwe",
            "ringFamily": "power2",
            "targetSecurity": 128,
            "securityModel": "classical",
            "redCostModel": "matzov",
            "nttScalePower": 1,
            "maxQBits": 24,
            "distribution": "auto",
        })

        candidate = result["recommendation"]
        self.assertEqual(candidate["ring"]["n"], 512)
        self.assertEqual(candidate["modulus"]["q"], 257)
        self.assertEqual(candidate["distribution"]["name"], "ST(l0=2, l1=0)")

    def test_lwr_variants_use_uniform_error_with_configurable_secret(self):
        request = parse_request({
            "hardProblemCategory": "LWE",
            "hardProblemVariant": "LWR",
            "distribution": "centered_binomial",
        })

        self.assertEqual(request.distribution, "centered_binomial")

        result = recommend_rlwe({
            "hardProblemCategory": "LWE",
            "hardProblemVariant": "MLWR",
            "targetSecurity": 128,
            "securityModel": "classical",
            "redCostModel": "matzov",
            "nttScalePower": 1,
            "maxQBits": 24,
            "distribution": "sparse_ternary",
        })

        candidate = result["recommendation"]
        self.assertEqual(result["request"]["distribution"], "sparse_ternary")
        self.assertEqual(candidate["distribution"]["secret"]["family"], "sparse_ternary")
        self.assertEqual(candidate["distribution"]["error"]["family"], "uniform")
        self.assertEqual(candidate["distribution"]["error"]["estimator"]["type"], "uniform")
        lower_bound, upper_bound = candidate["lwr"]["error_support"]
        self.assertEqual(candidate["lwr"]["p"], upper_bound - lower_bound + 1)
        self.assertEqual(candidate["lwr"]["p"] % 2, 1)

        with self.assertRaises(ValueError):
            parse_request({
                "hardProblemCategory": "LWE",
                "hardProblemVariant": "LWE",
                "distribution": "uniform",
            })

    def test_lwr_auto_prefers_smallest_uniform_error_support(self):
        result = recommend_rlwe({
            "hardProblemCategory": "LWE",
            "hardProblemVariant": "RLWR",
            "targetSecurity": 128,
            "securityModel": "classical",
            "redCostModel": "matzov",
            "nttScalePower": 1,
            "maxQBits": 24,
            "distribution": "auto",
        })

        candidate = result["recommendation"]
        self.assertEqual(candidate["ring"]["n"], 512)
        self.assertEqual(candidate["modulus"]["q"], 257)
        self.assertEqual(candidate["distribution"]["error"]["name"], "Uniform(-1,1)")
        self.assertEqual(candidate["lwr"]["p"], 3)
        self.assertTrue(candidate["selection"]["meets_target"])

    def test_uniform_distribution_uses_nd_uniform_estimator(self):
        spec = uniform_spec(2)
        self.assertEqual(spec.estimator["type"], "uniform")
        self.assertEqual(spec.estimator["lower_bound"], -2)
        self.assertEqual(spec.estimator["upper_bound"], 2)

        class FakeND:
            @staticmethod
            def Uniform(lower_bound, upper_bound):
                return ("uniform", lower_bound, upper_bound)

        self.assertEqual(
            estimator_distribution(FakeND, {"estimator": spec.estimator}, 512),
            ("uniform", -2, 2),
        )

    def test_lwr_p_comes_from_uniform_error_support_size(self):
        profile = lwr_rounding_profile(uniform_spec(1))

        self.assertEqual(profile["error_support"], [-1, 1])
        self.assertEqual(profile["p"], 3)

    def test_estimator_timeout_allows_five_minute_live_runs(self):
        request = parse_request({"estimatorTimeout": 999})

        self.assertEqual(request.estimator_timeout, 300)

    def test_remote_estimator_is_used_when_configured(self):
        result = recommend_rlwe({
            "targetSecurity": 128,
            "securityModel": "classical",
            "nttScalePower": 1,
            "maxQBits": 24,
            "distribution": "auto",
        })
        candidate = result["recommendation"]
        remote_result = {"ok": True, "modes": {"classical": {}, "quantum": {}}}
        config = AppConfig(
            estimator=EstimatorConfig(
                remote_url="https://example-estimator.hf.space",
                remote_timeout_seconds=240,
                remote_poll_interval_seconds=1.0,
            )
        )

        with patch("app.parameter_search.load_config", return_value=config):
            with patch("app.parameter_search.estimate_remotely", return_value=remote_result) as remote:
                self.assertIs(run_sage_estimator(candidate, 16), remote_result)

        remote.assert_called_once()
        _, kwargs = remote.call_args
        self.assertEqual(kwargs["base_url"], "https://example-estimator.hf.space")
        self.assertEqual(kwargs["timeout_seconds"], 240)
        self.assertEqual(kwargs["payload"]["problem"], "lwe")
        self.assertEqual(kwargs["payload"]["n"], candidate["ring"]["n"])
        self.assertEqual(kwargs["payload"]["q"], candidate["modulus"]["q"])

    def test_hard_problem_taxonomy_is_preserved(self):
        request = parse_request({
            "hardProblemCategory": "LWE",
            "hardProblemVariant": "MLWE",
        })

        self.assertEqual(request.hard_problem_category, "lwe")
        self.assertEqual(request.hard_problem_variant, "mlwe")

        request = parse_request({
            "hardProblemCategory": "LWE",
            "hardProblemVariant": "RLWE",
        })

        self.assertEqual(request.hard_problem_category, "lwe")
        self.assertEqual(request.hard_problem_variant, "rlwe")

        with self.assertRaises(ValueError):
            parse_request({
                "hardProblemCategory": "SIS",
                "hardProblemVariant": "MLWE",
            })

        with self.assertRaises(ValueError):
            parse_request({
                "securityModel": "matzov",
            })

        with self.assertRaises(ValueError):
            parse_request({
                "securityModel": "min",
            })

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

    def test_matzov_reduction_model_128_recommendations_are_tight_lower_bound(self):
        request = {
            "targetSecurity": 128,
            "securityModel": "classical",
            "redCostModel": "matzov",
            "maxQBits": 24,
        }
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
