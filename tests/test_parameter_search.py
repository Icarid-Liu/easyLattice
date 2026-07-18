import unittest
from unittest.mock import patch

from app.config import AppConfig
from app.parameter_search import (
    compression_noise_spec,
    factor_integer,
    is_prime,
    ntt_prime_candidates,
    recommend_rlwe,
    rotate_secret_candidates,
    run_sage_estimator,
    security_margin_bits,
    security_level_for_bits,
    secret_validation_key,
    parse_request,
    sparse_ternary_spec,
    uniform_spec,
    lwr_rounding_profile,
    ring_dimensions,
)
from app.estimator_runner import estimator_distribution
from app.compression_noise import compression_noise_pdf, compression_noise_profile
from app.security_result import modulus_bits, selection_status, validation_result


def estimator_success(bits=140.0, complete=True, profile="enhanced", commit="abc1234"):
    models = {
        model: {
            mode: {
                "ok": True,
                "complete": complete,
                "min_bits": bits - (1.0 if mode == "quantum" else 0.0),
                "best_attack": "usvp",
                "attacks": {},
            }
            for mode in ("classical", "quantum")
        }
        for model in ("matzov", "adps16")
    }
    return {
        "ok": True,
        "complete": complete,
        "estimator_profile": profile,
        "estimator_commit": commit,
        "modes": models["adps16"],
        "models": models,
    }


def estimator_partial_single_mode(bits=149.0):
    failed_mode = {
        "ok": False,
        "complete": False,
        "message": "no attack estimate completed",
        "attacks": {},
    }
    models = {
        model: {
            mode: dict(failed_mode)
            for mode in ("classical", "quantum")
        }
        for model in ("matzov", "adps16")
    }
    models["matzov"]["classical"] = {
        "ok": True,
        "complete": True,
        "min_bits": bits,
        "best_attack": "usvp",
        "attacks": {},
    }
    return {
        "ok": False,
        "complete": False,
        "estimator_profile": "enhanced",
        "estimator_commit": "partial123",
        "modes": models["adps16"],
        "models": models,
    }


def small_validation_request(**overrides):
    request = {
        "hardProblemCategory": "lwe",
        "hardProblemVariant": "rlwr",
        "targetSecurity": 40,
        "securityModel": "classical",
        "redCostModel": "matzov",
        "ringFamily": "power2",
        "minN": 512,
        "maxN": 512,
        "minQBits": 9,
        "maxQBits": 9,
        "nttScalePower": 1,
        "secretDistribution": "centered_binomial",
        "errorDistribution": "3",
        "useEstimator": True,
    }
    request.update(overrides)
    return request


class ParameterSearchTests(unittest.TestCase):
    def test_modulus_bits_uses_ceil_log2(self):
        self.assertEqual(modulus_bits(2048), 11)
        self.assertEqual(modulus_bits(2049), 12)
        self.assertEqual(modulus_bits(8192), 13)
        with self.assertRaises(ValueError):
            modulus_bits(1)

    def test_validation_contract_distinguishes_all_states(self):
        self.assertEqual(
            validation_result(False, "enhanced", 0, 0, 0, 8, True)["status"],
            "not_requested",
        )
        self.assertEqual(
            validation_result(True, "enhanced", 2, 0, 0, 8, False)["status"],
            "failed",
        )
        self.assertEqual(
            validation_result(True, "enhanced", 2, 2, 2, 8, True)["status"],
            "partial",
        )
        validated = validation_result(
            True,
            "enhanced",
            8,
            8,
            8,
            8,
            True,
            estimator_commit="abc1234",
            message_codes=["validation_applied", "validation_applied"],
        )
        self.assertEqual(validated["status"], "validated")
        self.assertEqual(validated["estimator_commit"], "abc1234")
        self.assertEqual(validated["attempted_candidates"], 8)
        self.assertEqual(validated["successful_candidates"], 8)
        self.assertEqual(validated["covered_candidates"], 8)
        self.assertEqual(validated["eligible_candidates"], 8)
        self.assertEqual(validated["message_codes"], ["validation_applied"])
        self.assertEqual(selection_status(True), "target_met")
        self.assertEqual(selection_status(False), "target_unmet")

    def test_validation_scheduler_rotates_secret_distributions(self):
        def candidate(secret, rank):
            return {
                "rank": rank,
                "distribution": {
                    "secret": {"estimator": secret},
                },
            }

        cbd1 = {"type": "centered_binomial", "eta": 1}
        cbd2 = {"eta": 2, "type": "centered_binomial"}
        sparse = {"type": "sparse_ternary_fixed_weight", "plus_weight": 64, "minus_weight": 64}
        candidates = [
            candidate(cbd1, 1),
            candidate(cbd1, 2),
            candidate(cbd2, 3),
            candidate(sparse, 4),
        ]

        scheduled = rotate_secret_candidates(candidates, lambda item: item["rank"])

        self.assertEqual(len({secret_validation_key(item) for item in scheduled[:3]}), 3)
        self.assertEqual(scheduled[-1]["rank"], 2)
        self.assertEqual(
            secret_validation_key(candidate({"eta": 2, "type": "centered_binomial"}, 0)),
            secret_validation_key(candidate({"type": "centered_binomial", "eta": 2}, 0)),
        )

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

    def test_structured_prime_generation_enforces_minimum_modulus_bits(self):
        primes = ntt_prime_candidates(
            512,
            max_q_bits=10,
            ntt_scale_power=1,
            min_q_bits=10,
            limit=20,
        )

        self.assertIn(769, primes)
        self.assertNotIn(257, primes)
        self.assertTrue(all(modulus_bits(q) == 10 for q in primes))

    def test_default_recommendation_has_rlwe_shape(self):
        result = recommend_rlwe({"targetSecurity": 128, "securityModel": "classical"})
        candidate = result["recommendation"]
        n = candidate["ring"]["n"]
        q = candidate["modulus"]["q"]
        self.assertGreaterEqual(candidate["selection"]["selected_security_bits"], 128)
        self.assertEqual((q - 1) % n, 0)
        self.assertIn(candidate["distribution"]["secret"]["family"], {"centered_binomial", "sparse_ternary"})
        self.assertIn(candidate["distribution"]["error"]["family"], {"centered_binomial", "sparse_ternary"})
        self.assertEqual(candidate["selection"]["security_level"], "NIST-I")
        self.assertIn("visual_scores", candidate)
        self.assertEqual(candidate["visual_scores"]["security"]["max_bits"], 512)
        self.assertGreater(candidate["visual_scores"]["compactness"]["score"], 0)
        self.assertEqual(candidate["visual_scores"]["performance"]["score"], 1.0)
        self.assertEqual(candidate["selection"]["status"], "target_met")
        self.assertEqual(candidate["security"]["source_code"], "fast_screen")
        self.assertIn("screen_scheme_not_bound", candidate["warning_codes"])
        self.assertEqual(result["validation"]["status"], "not_requested")
        self.assertEqual(result["validation"]["profile"], "enhanced")
        self.assertEqual(result["next_step_code"], "bind_scheme_constraints")

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

    def test_classical_auto_candidate_searches_secret_and_error_separately(self):
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
        self.assertEqual(candidate["distribution"]["secret"]["family"], "sparse_ternary")
        self.assertEqual(candidate["distribution"]["error"]["family"], "sparse_ternary")
        self.assertNotEqual(candidate["distribution"]["secret"]["name"], candidate["distribution"]["error"]["name"])

    def test_lwr_variants_use_compression_noise_error_with_configurable_secret(self):
        request = parse_request({
            "hardProblemCategory": "LWE",
            "hardProblemVariant": "LWR",
            "secretDistribution": "centered_binomial",
            "errorDistribution": "5",
        })

        self.assertEqual(request.secret_distribution, "centered_binomial")
        self.assertEqual(request.error_distribution, "5")

        result = recommend_rlwe({
            "hardProblemCategory": "LWE",
            "hardProblemVariant": "MLWR",
            "targetSecurity": 128,
            "securityModel": "classical",
            "redCostModel": "matzov",
            "nttScalePower": 1,
            "maxQBits": 24,
            "secretDistribution": "sparse_ternary",
            "errorDistribution": "3",
        })

        candidate = result["recommendation"]
        self.assertEqual(result["request"]["secret_distribution"], "sparse_ternary")
        self.assertEqual(result["request"]["error_distribution"], "3")
        self.assertEqual(candidate["distribution"]["secret"]["family"], "sparse_ternary")
        self.assertEqual(candidate["distribution"]["error"]["family"], "compression_noise")
        self.assertEqual(candidate["distribution"]["error"]["estimator"]["type"], "compression_noise")
        lower_bound, upper_bound = candidate["lwr"]["error_support"]
        self.assertEqual(candidate["lwr"]["p"], 3)
        self.assertLess(lower_bound, 0)
        self.assertGreater(upper_bound, 0)

        with self.assertRaises(ValueError):
            parse_request({
                "hardProblemCategory": "LWE",
                "hardProblemVariant": "LWE",
                "distribution": "uniform",
            })

    def test_lwr_default_error_uses_p3_compression_noise(self):
        result = recommend_rlwe({
            "hardProblemCategory": "LWE",
            "hardProblemVariant": "RLWR",
            "targetSecurity": 128,
            "securityModel": "classical",
            "redCostModel": "matzov",
            "nttScalePower": 1,
            "maxQBits": 24,
            "secretDistribution": "auto",
        })

        candidate = result["recommendation"]
        self.assertEqual(candidate["ring"]["n"], 512)
        self.assertEqual(candidate["modulus"]["q"], 257)
        self.assertEqual(candidate["distribution"]["error"]["name"], "CompressNoise(p=3)")
        self.assertEqual(candidate["lwr"]["p"], 3)
        self.assertTrue(candidate["selection"]["meets_target"])

    def test_compression_noise_moments_match_pointwise_pdf(self):
        q = 17
        p = 3
        profile = compression_noise_profile(q, p)
        pdf = compression_noise_pdf(q, p)
        mean = sum(value * probability for value, probability in pdf.items())
        variance = sum(value * value * probability for value, probability in pdf.items()) - mean * mean

        self.assertEqual(profile.support, [min(pdf), max(pdf)])
        self.assertAlmostEqual(profile.mean, float(mean))
        self.assertAlmostEqual(profile.variance, float(variance))
        self.assertEqual(sum(pdf.values()), 1)

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

    def test_compression_noise_uses_nd_noise_distribution_estimator(self):
        spec = compression_noise_spec(257, 3)

        class FakeND:
            @staticmethod
            def NoiseDistribution(n, mean, stddev, bounds, _density):
                return ("noise", n, mean, stddev, bounds, _density)

        result = estimator_distribution(FakeND, {"estimator": spec.estimator}, 512)

        self.assertEqual(result[0], "noise")
        self.assertEqual(result[1], 512)
        self.assertEqual(result[4], tuple(spec.support))

    def test_lwr_rounding_profile_reports_compression_p(self):
        profile = lwr_rounding_profile(compression_noise_spec(257, 3))

        self.assertEqual(profile["p"], 3)
        self.assertEqual(profile["rounding_modulus"], 3)
        self.assertIn("compression", profile["note"])

    def test_security_level_for_bits(self):
        self.assertEqual(security_level_for_bits(127.9), "below NIST-I")
        self.assertEqual(security_level_for_bits(128), "NIST-I")
        self.assertEqual(security_level_for_bits(192), "NIST-III")
        self.assertEqual(security_level_for_bits(256), "NIST-V")

    def test_estimator_timeout_allows_five_minute_live_runs(self):
        request = parse_request({"estimatorTimeout": 999})

        self.assertEqual(request.estimator_timeout, 300)

    def test_run_sage_estimator_routes_profiles_and_structured_payload_fields(self):
        candidate = {
            "ring": {"n": 512},
            "modulus": {"q": 257},
            "distribution": {
                "name": "CBD(2)",
                "secret": {"estimator": {"type": "centered_binomial", "eta": 2}},
                "error": {"estimator": {"type": "centered_binomial", "eta": 2}},
            },
        }
        expected_profiles = {
            "lwe": "standard",
            "lwr": "standard",
            "rlwe": "enhanced",
            "mlwe": "enhanced",
            "rlwr": "enhanced",
            "mlwr": "enhanced",
        }

        with patch(
            "app.parameter_search.run_estimator",
            return_value={"ok": False, "code": "estimator_timeout", "message": "timeout"},
        ) as run:
            for variant, expected_profile in expected_profiles.items():
                with self.subTest(variant=variant):
                    request = parse_request({
                        "hardProblemCategory": "lwe",
                        "hardProblemVariant": variant,
                    })
                    run_sage_estimator(candidate, 16, config=AppConfig(), request=request)
                    payload, timeout, _, profile = run.call_args.args
                    self.assertEqual(timeout, 16)
                    self.assertEqual(profile, expected_profile)
                    self.assertEqual(payload["hard_problem_variant"], variant)
                    self.assertEqual(payload["ring_degree"], 512)

    def test_mocked_validation_rotates_secrets_and_reports_partial_coverage(self):
        returned_bits = iter((141.0, 151.0))

        def estimate(*args):
            return estimator_success(bits=next(returned_bits))

        with patch("app.parameter_search.run_estimator", side_effect=estimate) as run:
            result = recommend_rlwe(
                small_validation_request(validationCount=2, validationAttempts=2),
                config=AppConfig(),
            )

        secret_descriptors = [call.args[0]["secret_distribution"]["estimator"] for call in run.call_args_list]
        self.assertEqual(len({str(sorted(descriptor.items())) for descriptor in secret_descriptors}), 2)
        self.assertEqual(result["validation"]["status"], "partial")
        self.assertEqual(result["validation"]["attempted_candidates"], 2)
        self.assertEqual(result["validation"]["successful_candidates"], 2)
        self.assertEqual(result["validation"]["covered_candidates"], 2)
        self.assertEqual(result["validation"]["eligible_candidates"], 7)
        self.assertEqual(result["validation"]["estimator_commit"], "abc1234")
        self.assertEqual(result["recommendation"]["selection"]["selected_security_bits"], 151.0)
        self.assertEqual(result["recommendation"]["security"]["source_code"], "sage_enhanced")
        self.assertIn("validation_applied", result["recommendation"]["warning_codes"])

    def test_partial_estimator_models_preserve_finite_mode_results(self):
        with patch(
            "app.parameter_search.run_estimator",
            return_value=estimator_partial_single_mode(),
        ):
            result = recommend_rlwe(
                small_validation_request(validationCount=1, validationAttempts=1),
                config=AppConfig(),
            )

        self.assertEqual(result["validation"]["status"], "partial")
        self.assertEqual(result["validation"]["attempted_candidates"], 1)
        self.assertEqual(result["validation"]["successful_candidates"], 1)
        self.assertEqual(result["validation"]["covered_candidates"], 1)
        self.assertEqual(result["validation"]["estimator_commit"], "partial123")
        self.assertEqual(result["recommendation"]["security"]["source_code"], "sage_enhanced")
        self.assertEqual(result["recommendation"]["security"]["matzov_bits"], 149.0)
        self.assertIsNone(result["recommendation"]["security"]["adps16_core_svp_bits"])
        self.assertEqual(result["recommendation"]["selection"]["selected_security_bits"], 149.0)
        self.assertIn("validation_partial_attacks", result["validation"]["message_codes"])
        self.assertFalse(result["estimator"]["validated"][0]["ok"])

    def test_malformed_estimator_responses_fail_without_candidate_mutation(self):
        empty_models = {
            "ok": True,
            "complete": True,
            "estimator_profile": "enhanced",
            "models": {},
            "modes": {},
        }
        wrong_profile = estimator_success(profile="standard")
        non_dict_models = estimator_success()
        non_dict_models["models"] = []
        non_dict_modes = estimator_success()
        non_dict_modes["modes"] = []
        invalid_top_complete = estimator_success()
        invalid_top_complete["complete"] = "yes"
        invalid_mode_complete = estimator_success()
        invalid_mode_complete["models"]["matzov"]["classical"]["complete"] = 1
        malformed_bits = estimator_success()
        malformed_bits["models"]["matzov"]["classical"]["min_bits"] = "149"
        nonfinite_bits = estimator_success()
        nonfinite_bits["models"]["matzov"]["classical"]["min_bits"] = float("inf")
        nan_bits = estimator_success()
        nan_bits["models"]["matzov"]["classical"]["min_bits"] = float("nan")
        cases = {
            "non_object": "not an estimator response",
            "empty_models": empty_models,
            "wrong_profile": wrong_profile,
            "non_dict_models": non_dict_models,
            "non_dict_modes": non_dict_modes,
            "invalid_top_complete": invalid_top_complete,
            "invalid_mode_complete": invalid_mode_complete,
            "malformed_bits": malformed_bits,
            "nonfinite_bits": nonfinite_bits,
            "nan_bits": nan_bits,
        }

        for name, response in cases.items():
            with self.subTest(name=name):
                with patch("app.parameter_search.run_estimator", return_value=response):
                    result = recommend_rlwe(
                        small_validation_request(validationCount=1, validationAttempts=1),
                        config=AppConfig(),
                    )

                self.assertEqual(result["validation"]["status"], "failed")
                self.assertEqual(result["validation"]["attempted_candidates"], 1)
                self.assertEqual(result["validation"]["successful_candidates"], 0)
                self.assertEqual(result["validation"]["covered_candidates"], 0)
                self.assertIn("Invalid estimator response", result["validation"]["message"])
                self.assertEqual(result["recommendation"]["security"]["source_code"], "fast_screen")
                self.assertEqual(
                    result["estimator"]["validated"][0]["code"],
                    "invalid_estimator_response",
                )

    def test_estimator_failure_keeps_fast_screen_and_preserves_unknown_message(self):
        failures = [
            {
                "ok": False,
                "code": "sage_not_found",
                "message": "Sage is not installed.",
            },
            {
                "ok": False,
                "code": "future_estimator_failure",
                "message": "opaque estimator failure",
            },
        ]
        with patch("app.parameter_search.run_estimator", side_effect=failures):
            result = recommend_rlwe(
                small_validation_request(validationCount=2, validationAttempts=2),
                config=AppConfig(),
            )

        self.assertEqual(result["validation"]["status"], "failed")
        self.assertEqual(result["validation"]["attempted_candidates"], 2)
        self.assertEqual(result["validation"]["successful_candidates"], 0)
        self.assertEqual(result["validation"]["covered_candidates"], 0)
        self.assertEqual(result["validation"]["message"], "opaque estimator failure")
        self.assertEqual(result["validation"]["messages"], ["opaque estimator failure"])
        self.assertIn("validation_config_missing", result["validation"]["message_codes"])
        self.assertEqual(result["recommendation"]["security"]["source_code"], "fast_screen")
        self.assertIn("validation_config_missing", result["recommendation"]["warning_codes"])
        self.assertEqual(result["estimator"]["validated"][1]["message"], "opaque estimator failure")

    def test_complete_coverage_with_partial_attacks_is_partial(self):
        with patch(
            "app.parameter_search.run_estimator",
            return_value=estimator_success(complete=False),
        ) as run:
            result = recommend_rlwe(
                small_validation_request(validationCount=7, validationAttempts=80),
                config=AppConfig(),
            )

        self.assertEqual(run.call_count, 7)
        self.assertEqual(result["validation"]["covered_candidates"], 7)
        self.assertEqual(result["validation"]["eligible_candidates"], 7)
        self.assertEqual(result["validation"]["status"], "partial")
        self.assertIn("validation_partial_attacks", result["validation"]["message_codes"])
        self.assertIn("validation_partial_attacks", result["recommendation"]["warning_codes"])

    def test_target_unmet_candidate_is_explicit_analytical_result(self):
        result = recommend_rlwe({
            "targetSecurity": 512,
            "minN": 512,
            "maxN": 512,
            "minQBits": 9,
            "maxQBits": 9,
            "nttScalePower": 1,
            "secretDistribution": "centered_binomial",
            "errorDistribution": "centered_binomial",
            "useEstimator": False,
        })

        self.assertEqual(result["recommendation"]["selection"]["status"], "target_unmet")
        self.assertFalse(result["recommendation"]["selection"]["meets_target"])
        self.assertEqual(result["validation"]["status"], "not_requested")

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

        for category, variant in (("ntru", "ring"), ("sis", "sis")):
            with self.subTest(category=category):
                with self.assertRaisesRegex(
                    ValueError,
                    "^LWE parameter search requires hard_problem_category=lwe\\.$",
                ):
                    recommend_rlwe({
                        "hardProblemCategory": category,
                        "hardProblemVariant": variant,
                    })

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
