import json
import math
import unittest
from unittest.mock import patch

from app.config import AppConfig, EstimatorConfig
from app.ntru_search import (
    apply_ntru_estimator_result,
    ntru_candidate_specs,
    parse_ntru_request,
    recommend_ntru,
    run_ntru_estimator,
)


def estimator_success(bits=140.0, complete=True, commit="ntru123"):
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
        "estimator_profile": "standard",
        "estimator_commit": commit,
        "modes": models["adps16"],
        "models": models,
    }


def estimator_partial_single_mode(bits=149.0, model="matzov", mode="classical"):
    failed_mode = {
        "ok": False,
        "complete": False,
        "message": "no attack estimate completed",
        "attacks": {},
    }
    models = {
        model_name: {
            mode_name: dict(failed_mode)
            for mode_name in ("classical", "quantum")
        }
        for model_name in ("matzov", "adps16")
    }
    models[model][mode] = {
        "ok": True,
        "complete": True,
        "min_bits": bits,
        "best_attack": "usvp",
        "attacks": {},
    }
    return {
        "ok": False,
        "complete": False,
        "estimator_profile": "standard",
        "estimator_commit": "partial-ntru",
        "modes": models["adps16"],
        "models": models,
    }


class NTRUSearchTests(unittest.TestCase):
    def test_power2_ntru_recommendation_has_three_candidates(self):
        result = recommend_ntru(
            {
                "targetSecurity": 128,
                "ringFamily": "power2",
                "useEstimator": False,
            }
        )
        options = [result["recommendation"], *result["alternatives"][:2]]

        self.assertEqual(result["request"]["problem"], "ntru")
        self.assertEqual(len(options), 3)
        self.assertTrue(all(option["ring"]["family_id"] == "power2" for option in options))
        self.assertTrue(all(option["ring"]["ntru_type"] == "circulant" for option in options))
        self.assertTrue(all(option["selection"]["selected_security_bits"] >= 128 for option in options))
        self.assertEqual([option["modulus"]["q"] for option in options], [257, 769, 3329])
        self.assertEqual(
            [option["distribution"]["name"] for option in options],
            [
                "ST(l0=4, l1=2) + ST(l0=4, l1=0) + ST(l0=4, l1=0)",
                "ST(l0=3, l1=1) + CBD(1)",
                "ST(l0=2, l1=1) + ST(l0=2, l1=0) + CBD(4)",
            ],
        )
        for option in options:
            self.assertEqual((option["modulus"]["q"] - 1) % (option["ring"]["n"] // 2), 0)
            self.assertIn("n/2", option["modulus"]["ntt_condition"])
            calibration = option["distribution"]["calibration"]
            self.assertEqual(calibration["method"], "gaussian_proxy_then_fast_distribution")
            self.assertGreaterEqual(option["distribution"]["secret"]["stddev"], calibration["sigma_lower_bound"])
            self.assertEqual(option["distribution"]["secret"]["family"], "composite")
        profile = result["recommendation"]["visual_scores"]
        self.assertEqual(profile["security"]["max_bits"], 512)
        self.assertAlmostEqual(profile["security"]["score"], 0.25)
        self.assertEqual(profile["performance"]["k"], 2.0)
        self.assertEqual(profile["performance"]["score"], 0.5)

    def test_power2_honors_matrix_and_ring_variants(self):
        for variant, expected in (("matrix", "matrix"), ("ring", "circulant")):
            with self.subTest(variant=variant):
                result = recommend_ntru(
                    {
                        "targetSecurity": 128,
                        "hardProblemCategory": "ntru",
                        "hardProblemVariant": variant,
                        "ringFamily": "power2",
                        "useEstimator": False,
                    }
                )

                self.assertEqual(result["recommendation"]["ring"]["ntru_type"], expected)

    def test_hps_and_hrss_are_always_circulant(self):
        for family in ("hps", "hrss"):
            with self.subTest(family=family):
                result = recommend_ntru(
                    {
                        "ringFamily": family,
                        "hardProblemCategory": "ntru",
                        "hardProblemVariant": "matrix",
                        "targetSecurity": 128,
                    }
                )

                self.assertEqual(result["recommendation"]["ring"]["ntru_type"], "circulant")

    def test_all_official_sntrup_presets_are_available(self):
        request = parse_ntru_request(
            {
                "ringFamily": "ntru_prime",
                "minN": 1,
                "maxN": 2000,
                "minQBits": 2,
                "maxQBits": 24,
            }
        )
        specs = ntru_candidate_specs(request)

        self.assertEqual(
            [
                (
                    spec.preset,
                    spec.n,
                    spec.q,
                    spec.fixed_weight,
                    spec.screen_bits,
                    spec.screen_quantum_bits,
                    spec.nist_category,
                )
                for spec in specs
            ],
            [
                ("sntrup653", 653, 4621, 288, 129.0, 117.0, 1),
                ("sntrup761", 761, 4591, 286, 153.0, 139.0, 2),
                ("sntrup857", 857, 5167, 322, 175.0, 159.0, 3),
                ("sntrup953", 953, 6343, 396, 196.0, 178.0, 4),
                ("sntrup1013", 1013, 7177, 448, 209.0, 190.0, 4),
                ("sntrup1277", 1277, 7879, 492, 270.0, 245.0, 5),
            ],
        )
        self.assertTrue(all(spec.polynomial == f"x^{spec.n} - x - 1" for spec in specs))
        self.assertTrue(all(spec.quotient == f"Z_{spec.q}[x] / ({spec.polynomial})" for spec in specs))
        self.assertTrue(all(spec.ntru_type == "circulant" for spec in specs))
        for spec in specs:
            self.assertEqual(spec.secret_distribution["estimator"]["plus_weight"], spec.fixed_weight // 2)
            self.assertEqual(spec.secret_distribution["estimator"]["minus_weight"], spec.fixed_weight // 2)
            self.assertEqual(spec.error_distribution["estimator"], {"type": "uniform_mod", "modulus": 3})
            self.assertEqual(spec.screen_attack, "official-including-hybrid-minimum")
            self.assertIn("balanced estimator approximation", spec.note)

    def test_ntru_prime_candidate_has_field_aware_common_contract(self):
        result = recommend_ntru({"ringFamily": "ntru_prime", "targetSecurity": 128})
        candidate = result["recommendation"]
        ring_fields = {
            "family_id",
            "family",
            "n",
            "cyclotomic_index",
            "polynomial",
            "quotient",
            "ntru_type",
            "preset",
        }

        self.assertTrue(ring_fields.issubset(candidate["ring"]))
        self.assertIsNone(candidate["ring"]["cyclotomic_index"])
        self.assertEqual(candidate["ring"]["preset"], "sntrup653")
        self.assertEqual(candidate["ring"]["ntru_type"], "circulant")
        self.assertEqual(candidate["modulus"]["bits"], 13)
        self.assertIsNone(candidate["modulus"]["ntt_condition"])
        self.assertEqual(candidate["security"]["classical_bits"], 129.0)
        self.assertEqual(candidate["security"]["quantum_bits"], 117.0)
        self.assertEqual(candidate["security"]["nist_category"], 1)
        self.assertEqual(candidate["security"]["source_code"], "ntru_reference_screen")
        self.assertEqual(candidate["selection"]["status"], "target_met")
        self.assertEqual(result["validation"]["status"], "not_requested")
        self.assertEqual(result["next_step_code"], "bind_scheme_constraints")

    def test_all_families_have_common_nullable_fields(self):
        expected_ring_fields = {
            "family_id",
            "family",
            "n",
            "cyclotomic_index",
            "polynomial",
            "quotient",
            "ntru_type",
            "preset",
        }
        expected_ntt_fields = {
            "ntt_condition",
            "ntt_friendly",
            "ntt_quality",
            "ntt_layers_remaining",
            "polynomial_factorization",
            "factor_count",
            "factor_degree",
            "decomposition_score",
            "two_adicity",
            "small_factor_weight",
        }
        for family in ("power2", "hps", "hrss", "ntru_prime"):
            with self.subTest(family=family):
                candidate = recommend_ntru({"ringFamily": family})["recommendation"]
                self.assertTrue(expected_ring_fields.issubset(candidate["ring"]))
                self.assertTrue(expected_ntt_fields.issubset(candidate["modulus"]))
                if family == "power2":
                    self.assertEqual(candidate["ring"]["cyclotomic_index"], 2 * candidate["ring"]["n"])
                    self.assertIsNone(candidate["ring"]["preset"])
                else:
                    self.assertIsNone(candidate["ring"]["cyclotomic_index"])
                if family in ("hps", "hrss", "ntru_prime"):
                    self.assertTrue(all(candidate["modulus"][field] is None for field in expected_ntt_fields))

    def test_modulus_filters_use_ceil_log2_width(self):
        result = recommend_ntru(
            {
                "ringFamily": "hps",
                "minQBits": 11,
                "maxQBits": 11,
            }
        )

        self.assertEqual(result["recommendation"]["modulus"]["q"], 2048)
        self.assertEqual(result["recommendation"]["modulus"]["bits"], 11)

    def test_ntru_target_unmet_is_explicit(self):
        result = recommend_ntru({"ringFamily": "power2", "targetSecurity": 256})

        self.assertEqual(result["recommendation"]["selection"]["status"], "target_unmet")
        self.assertFalse(result["recommendation"]["selection"]["meets_target"])
        self.assertEqual(result["validation"]["status"], "not_requested")

    def test_ntru_estimator_timeout_allows_five_minute_live_runs(self):
        request = parse_ntru_request({"estimatorTimeout": 999})

        self.assertEqual(request.estimator_timeout, 300)

    def test_ntru_validation_attempts_remains_an_independent_cap(self):
        request = parse_ntru_request(
            {
                "validationCount": 3,
                "validationAttempts": 2,
            }
        )

        self.assertEqual(request.validation_count, 3)
        self.assertEqual(request.validation_attempts, 2)

    def test_old_family_quantum_without_estimator_is_json_safe_and_explicit(self):
        for family in ("power2", "hps", "hrss"):
            with self.subTest(family=family):
                request = {
                    "ringFamily": family,
                    "securityModel": "quantum",
                    "useEstimator": False,
                }
                result = recommend_ntru(request)
                repeated = recommend_ntru(request)
                candidate = result["recommendation"]
                selection = candidate["selection"]

                self.assertIsNone(selection["selected_security_bits"])
                self.assertIsNone(selection["margin_bits"])
                self.assertFalse(selection["meets_target"])
                self.assertEqual(selection["status"], "target_unmet")
                self.assertEqual(selection["security_level"], "unclassified")
                self.assertTrue(
                    all(
                        not isinstance(value, (int, float)) or math.isfinite(value)
                        for value in selection["rank_score"]
                    )
                )
                self.assertIsNone(candidate["security"]["quantum_bits"])
                self.assertIsNone(candidate["security"]["ntru_bits"])
                self.assertIsNone(candidate["visual_scores"]["security"]["bits"])
                self.assertIn("quantum_estimate_unavailable", candidate["warning_codes"])
                self.assertEqual(result["validation"]["status"], "not_requested")
                self.assertIn(
                    "quantum_estimate_unavailable",
                    result["validation"]["message_codes"],
                )
                self.assertEqual(
                    (
                        candidate["ring"]["family_id"],
                        candidate["ring"]["n"],
                        candidate["modulus"]["q"],
                        selection["rank_score"],
                    ),
                    (
                        repeated["recommendation"]["ring"]["family_id"],
                        repeated["recommendation"]["ring"]["n"],
                        repeated["recommendation"]["modulus"]["q"],
                        repeated["recommendation"]["selection"]["rank_score"],
                    ),
                )
                json.dumps(result, allow_nan=False)

    def test_old_family_quantum_estimator_failure_is_json_safe_and_explicit(self):
        cases = {
            "power2": {"minN": 512, "maxN": 512, "minQBits": 9, "maxQBits": 9},
            "hps": {"minN": 592, "maxN": 592, "minQBits": 11, "maxQBits": 11},
            "hrss": {"minN": 672, "maxN": 672, "minQBits": 13, "maxQBits": 13},
        }
        failure = {
            "ok": False,
            "code": "standard_estimator_not_configured",
            "message": "standard estimator path is not configured.",
        }
        for family, bounds in cases.items():
            with self.subTest(family=family):
                with patch("app.ntru_search.run_estimator", return_value=failure):
                    result = recommend_ntru(
                        {
                            "ringFamily": family,
                            "securityModel": "quantum",
                            "useEstimator": True,
                            "validationCount": 1,
                            "validationAttempts": 1,
                            **bounds,
                        },
                        config=AppConfig(),
                    )
                candidate = result["recommendation"]
                selection = candidate["selection"]

                self.assertIsNone(selection["selected_security_bits"])
                self.assertIsNone(selection["margin_bits"])
                self.assertFalse(selection["meets_target"])
                self.assertEqual(selection["status"], "target_unmet")
                self.assertTrue(
                    all(
                        not isinstance(value, (int, float)) or math.isfinite(value)
                        for value in selection["rank_score"]
                    )
                )
                self.assertEqual(result["validation"]["status"], "failed")
                self.assertIn(
                    "quantum_estimate_unavailable",
                    result["validation"]["message_codes"],
                )
                self.assertIn("validation_config_missing", candidate["warning_codes"])
                self.assertIn("quantum_estimate_unavailable", candidate["warning_codes"])
                entry = result["estimator"]["validated"][0]
                self.assertEqual(entry["code"], failure["code"])
                self.assertEqual(entry["message"], failure["message"])
                self.assertEqual(entry["hard_problem_variant"], "ring")
                self.assertEqual(entry["ring_degree"], candidate["ring"]["n"])
                json.dumps(result, allow_nan=False)

    def test_ntru_estimator_uses_selected_model_for_classical_and_quantum_bits(self):
        candidate = recommend_ntru(
            {
                "targetSecurity": 128,
                "ringFamily": "hps",
                "useEstimator": False,
            }
        )["recommendation"]
        request = parse_ntru_request(
            {
                "targetSecurity": 128,
                "ringFamily": "hps",
                "securityModel": "classical",
                "redCostModel": "matzov",
                "useEstimator": True,
            }
        )

        def mode(bits):
            return {"ok": True, "min_bits": bits, "best_attack": "usvp", "attacks": {}}

        estimator_result = {
            "ok": True,
            "models": {
                "matzov": {"classical": mode(141.2), "quantum": mode(125.5)},
                "adps16": {"classical": mode(139.4), "quantum": mode(121.0)},
            },
            "modes": {"classical": mode(139.4), "quantum": mode(121.0)},
        }
        apply_ntru_estimator_result(candidate, estimator_result, request)

        self.assertEqual(candidate["security"]["matzov_bits"], 141.2)
        self.assertEqual(candidate["security"]["matzov_quantum_bits"], 125.5)
        self.assertEqual(candidate["security"]["adps16_core_svp_bits"], 139.4)
        self.assertEqual(candidate["security"]["adps16_quantum_bits"], 121.0)
        self.assertEqual(candidate["selection"]["selected_security_bits"], 141.2)
        self.assertEqual(candidate["selection"]["security_level"], "NIST-I")

    def test_ntru_performance_is_max_for_n_or_2n_ntt_scale(self):
        n_scale_result = recommend_ntru(
            {
                "targetSecurity": 128,
                "ringFamily": "power2",
                "nttScalePower": 0,
                "useEstimator": False,
            }
        )
        full_scale_result = recommend_ntru(
            {
                "targetSecurity": 128,
                "ringFamily": "power2",
                "nttScalePower": -1,
                "useEstimator": False,
            }
        )

        self.assertEqual(n_scale_result["recommendation"]["visual_scores"]["performance"]["k"], 1.0)
        self.assertEqual(n_scale_result["recommendation"]["visual_scores"]["performance"]["score"], 1.0)
        self.assertEqual(full_scale_result["recommendation"]["visual_scores"]["performance"]["k"], 0.5)
        self.assertEqual(full_scale_result["recommendation"]["visual_scores"]["performance"]["score"], 1.0)

    def test_ntru_unfriendly_ntt_scale_uses_lift_profile(self):
        result = recommend_ntru(
            {
                "targetSecurity": 128,
                "ringFamily": "power2",
                "nttScalePower": 6,
                "useEstimator": False,
            }
        )

        performance = result["recommendation"]["visual_scores"]["performance"]
        self.assertEqual(performance["condition"], "no restriction of n and q (NTT unfriendly)")
        self.assertEqual(performance["score"], 0.0)
        self.assertEqual(performance["k_label"], "lift")

    def test_ntru_module_variant_is_not_supported(self):
        with self.assertRaisesRegex(ValueError, "hard_problem_variant for NTRU must be one of matrix, ring"):
            recommend_ntru(
                {
                    "targetSecurity": 128,
                    "hardProblemCategory": "ntru",
                    "hardProblemVariant": "module",
                    "ringFamily": "power2",
                    "useEstimator": False,
                }
            )

    def test_hps_below_128_screen_is_not_selected(self):
        result = recommend_ntru(
            {
                "targetSecurity": 128,
                "ringFamily": "hps",
                "useEstimator": False,
            }
        )

        self.assertGreaterEqual(result["recommendation"]["selection"]["selected_security_bits"], 128)
        self.assertGreaterEqual(result["recommendation"]["ring"]["n"], 592)

    def test_ntru_estimator_routes_through_standard_profile(self):
        result = recommend_ntru(
            {
                "targetSecurity": 128,
                "ringFamily": "power2",
                "useEstimator": False,
            }
        )
        candidate = result["recommendation"]
        estimator_result = estimator_success()
        config = AppConfig(
            estimator=EstimatorConfig(
                remote_url="https://example-estimator.hf.space",
                remote_timeout_seconds=300,
                remote_poll_interval_seconds=1.0,
            )
        )
        request = parse_ntru_request(
            {
                "hardProblemVariant": "matrix",
                "ringFamily": "power2",
                "useEstimator": True,
            },
            config=config,
        )

        with patch("app.ntru_search.run_estimator", return_value=estimator_result) as run:
            self.assertIs(run_ntru_estimator(candidate, 45, config=config, request=request), estimator_result)

        run.assert_called_once()
        payload, timeout, passed_config, profile = run.call_args.args
        self.assertEqual(timeout, 45)
        self.assertIs(passed_config, config)
        self.assertEqual(profile, "standard")
        self.assertEqual(payload["problem"], "ntru")
        self.assertEqual(payload["ntru_type"], "matrix")
        self.assertEqual(payload["hard_problem_variant"], "matrix")
        self.assertEqual(payload["requested_hard_problem_variant"], "matrix")
        self.assertEqual(payload["ring_degree"], candidate["ring"]["n"])
        self.assertIn("secret_distribution", payload)
        self.assertIn("error_distribution", payload)

    def test_forced_circulant_families_use_effective_ring_variant(self):
        cases = {
            "hps": {"minN": 592, "maxN": 592},
            "hrss": {"minN": 672, "maxN": 672},
            "ntru_prime": {"minN": 653, "maxN": 653},
        }
        for family, bounds in cases.items():
            with self.subTest(family=family):
                with patch(
                    "app.ntru_search.run_estimator",
                    return_value=estimator_success(),
                ) as run:
                    result = recommend_ntru(
                        {
                            "ringFamily": family,
                            "hardProblemVariant": "matrix",
                            "useEstimator": True,
                            "validationCount": 1,
                            "validationAttempts": 1,
                            **bounds,
                        },
                        config=AppConfig(),
                    )

                payload = run.call_args.args[0]
                entry = result["estimator"]["validated"][0]
                security = result["recommendation"]["security"]
                self.assertEqual(payload["ntru_type"], "circulant")
                self.assertEqual(payload["hard_problem_variant"], "ring")
                self.assertEqual(payload["requested_hard_problem_variant"], "matrix")
                self.assertEqual(entry["hard_problem_variant"], "ring")
                self.assertEqual(entry["requested_hard_problem_variant"], "matrix")
                self.assertEqual(security["hard_problem_variant"], "ring")
                self.assertEqual(security["requested_hard_problem_variant"], "matrix")

    def test_validation_count_caps_successful_coverage(self):
        with patch(
            "app.ntru_search.run_estimator",
            return_value=estimator_success(),
        ) as run:
            result = recommend_ntru(
                {
                    "ringFamily": "ntru_prime",
                    "useEstimator": True,
                    "validationCount": 3,
                    "validationAttempts": 5,
                },
                config=AppConfig(),
            )

        self.assertEqual(run.call_count, 3)
        self.assertEqual(result["validation"]["attempted_candidates"], 3)
        self.assertEqual(result["validation"]["successful_candidates"], 3)
        self.assertEqual(result["validation"]["covered_candidates"], 3)
        self.assertEqual(result["validation"]["eligible_candidates"], 6)
        self.assertEqual(result["validation"]["status"], "partial")

    def test_validation_failures_use_remaining_attempt_budget(self):
        failure = {
            "ok": False,
            "code": "estimator_timeout",
            "message": "timed out",
        }
        responses = [
            failure,
            estimator_success(bits=141.0),
            failure,
            estimator_success(bits=142.0),
            estimator_success(bits=143.0),
        ]
        with patch(
            "app.ntru_search.run_estimator",
            side_effect=responses,
        ) as run:
            result = recommend_ntru(
                {
                    "ringFamily": "ntru_prime",
                    "useEstimator": True,
                    "validationCount": 3,
                    "validationAttempts": 5,
                },
                config=AppConfig(),
            )

        self.assertEqual(run.call_count, 5)
        self.assertEqual(result["validation"]["attempted_candidates"], 5)
        self.assertEqual(result["validation"]["successful_candidates"], 3)
        self.assertEqual(result["validation"]["covered_candidates"], 3)
        self.assertEqual(result["validation"]["status"], "partial")
        self.assertEqual(len(result["estimator"]["validated"]), 5)

    def test_complete_estimator_result_reports_validated_state(self):
        with patch("app.ntru_search.run_estimator", return_value=estimator_success(bits=151.0)):
            result = recommend_ntru(
                {
                    "ringFamily": "ntru_prime",
                    "minN": 653,
                    "maxN": 653,
                    "useEstimator": True,
                    "validationCount": 1,
                    "validationAttempts": 1,
                },
                config=AppConfig(),
            )

        self.assertEqual(result["validation"]["status"], "validated")
        self.assertEqual(result["validation"]["profile"], "standard")
        self.assertEqual(result["validation"]["attempted_candidates"], 1)
        self.assertEqual(result["validation"]["successful_candidates"], 1)
        self.assertEqual(result["validation"]["covered_candidates"], 1)
        self.assertEqual(result["validation"]["eligible_candidates"], 1)
        self.assertEqual(result["validation"]["estimator_commit"], "ntru123")
        self.assertEqual(result["recommendation"]["security"]["source_code"], "sage_standard")
        self.assertEqual(result["recommendation"]["selection"]["status"], "target_met")
        self.assertIn("validation_applied", result["recommendation"]["warning_codes"])

    def test_partial_estimator_models_preserve_finite_mode_results(self):
        with patch(
            "app.ntru_search.run_estimator",
            return_value=estimator_partial_single_mode(),
        ):
            result = recommend_ntru(
                {
                    "ringFamily": "ntru_prime",
                    "minN": 653,
                    "maxN": 653,
                    "useEstimator": True,
                    "validationCount": 1,
                    "validationAttempts": 1,
                },
                config=AppConfig(),
            )

        security = result["recommendation"]["security"]
        self.assertEqual(result["validation"]["status"], "partial")
        self.assertEqual(result["validation"]["successful_candidates"], 1)
        self.assertEqual(result["validation"]["covered_candidates"], 1)
        self.assertEqual(result["validation"]["estimator_commit"], "partial-ntru")
        self.assertIn("validation_partial_attacks", result["validation"]["message_codes"])
        self.assertFalse(result["estimator"]["validated"][0]["ok"])
        self.assertEqual(security["source_code"], "sage_standard")
        self.assertEqual(security["matzov_bits"], 149.0)
        self.assertIsNone(security["matzov_quantum_bits"])
        self.assertIsNone(security["adps16_core_svp_bits"])
        self.assertIsNone(security["adps16_quantum_bits"])
        self.assertEqual(result["recommendation"]["selection"]["selected_security_bits"], 149.0)

    def test_nested_nonfinite_attack_metadata_is_sanitized_without_losing_coverage(self):
        response = estimator_success(bits=151.0)
        attack = {
            "ok": True,
            "rop_bits": float("inf"),
            "diagnostics": {
                "nan": float("nan"),
                "limits": [float("inf"), float("-inf")],
            },
        }
        response["models"]["matzov"]["classical"]["attacks"] = {"usvp": attack}
        response["diagnostics"] = {"nested": {"nan": float("nan")}}

        with patch("app.ntru_search.run_estimator", return_value=response):
            result = recommend_ntru(
                {
                    "ringFamily": "ntru_prime",
                    "minN": 653,
                    "maxN": 653,
                    "useEstimator": True,
                    "validationCount": 1,
                    "validationAttempts": 1,
                },
                config=AppConfig(),
            )

        entry = result["estimator"]["validated"][0]
        attacks = result["recommendation"]["security"]["attacks"]
        self.assertEqual(result["validation"]["successful_candidates"], 1)
        self.assertEqual(result["validation"]["covered_candidates"], 1)
        self.assertIsNone(entry["diagnostics"]["nested"]["nan"])
        self.assertIsNone(
            attacks["matzov"]["classical"]["attacks"]["usvp"]["diagnostics"]["nan"]
        )
        self.assertIsNone(
            attacks["matzov"]["classical"]["attacks"]["usvp"]["rop_bits"]
        )
        self.assertEqual(
            attacks["matzov"]["classical"]["attacks"]["usvp"]["diagnostics"]["limits"],
            [None, None],
        )
        json.dumps(result, allow_nan=False)

    def test_malformed_estimator_responses_fail_without_candidate_mutation(self):
        malformed = estimator_success()
        malformed["models"]["matzov"]["classical"]["min_bits"] = float("inf")
        cases = {
            "non_object": "not an estimator response",
            "wrong_profile": estimator_success() | {"estimator_profile": "enhanced"},
            "nonfinite_bits": malformed,
        }

        for name, response in cases.items():
            with self.subTest(name=name):
                with patch("app.ntru_search.run_estimator", return_value=response):
                    result = recommend_ntru(
                        {
                            "ringFamily": "ntru_prime",
                            "minN": 653,
                            "maxN": 653,
                            "useEstimator": True,
                            "validationCount": 1,
                            "validationAttempts": 1,
                        },
                        config=AppConfig(),
                    )

                self.assertEqual(result["validation"]["status"], "failed")
                self.assertEqual(result["validation"]["attempted_candidates"], 1)
                self.assertEqual(result["validation"]["successful_candidates"], 0)
                self.assertEqual(result["validation"]["covered_candidates"], 0)
                self.assertIn("Invalid estimator response", result["validation"]["message"])
                self.assertEqual(result["recommendation"]["security"]["source_code"], "ntru_reference_screen")
                self.assertEqual(
                    result["estimator"]["validated"][0]["code"],
                    "invalid_estimator_response",
                )

    def test_estimator_failure_is_inspectable_and_keeps_reference_screen(self):
        failure = {
            "ok": False,
            "code": "future_estimator_failure",
            "message": "opaque NTRU estimator failure",
            "diagnostics": {
                "nan": float("nan"),
                "nested": [float("inf"), {"value": float("-inf")}],
            },
        }
        with patch("app.ntru_search.run_estimator", return_value=failure):
            result = recommend_ntru(
                {
                    "ringFamily": "ntru_prime",
                    "minN": 653,
                    "maxN": 653,
                    "useEstimator": True,
                    "validationCount": 1,
                    "validationAttempts": 1,
                },
                config=AppConfig(),
            )

        self.assertEqual(result["validation"]["status"], "failed")
        self.assertEqual(result["validation"]["message"], "opaque NTRU estimator failure")
        entry = result["estimator"]["validated"][0]
        self.assertEqual(entry["code"], failure["code"])
        self.assertEqual(entry["message"], failure["message"])
        self.assertIsNone(entry["diagnostics"]["nan"])
        self.assertEqual(entry["diagnostics"]["nested"], [None, {"value": None}])
        self.assertEqual(result["recommendation"]["security"]["source_code"], "ntru_reference_screen")
        self.assertIn("opaque NTRU estimator failure", result["recommendation"]["warnings"])
        json.dumps(result, allow_nan=False)

    def test_estimator_configuration_failure_uses_stable_warning_code(self):
        failure = {
            "ok": False,
            "code": "standard_estimator_not_configured",
            "message": "standard estimator path is not configured.",
        }
        with patch("app.ntru_search.run_estimator", return_value=failure):
            result = recommend_ntru(
                {
                    "ringFamily": "ntru_prime",
                    "minN": 653,
                    "maxN": 653,
                    "useEstimator": True,
                    "validationCount": 1,
                    "validationAttempts": 1,
                },
                config=AppConfig(),
            )

        self.assertEqual(result["validation"]["status"], "failed")
        self.assertIn("validation_config_missing", result["validation"]["message_codes"])
        self.assertIn("validation_config_missing", result["recommendation"]["warning_codes"])
        entry = result["estimator"]["validated"][0]
        self.assertEqual(entry["code"], failure["code"])
        self.assertEqual(entry["message"], failure["message"])
        self.assertEqual(entry["hard_problem_variant"], "ring")


if __name__ == "__main__":
    unittest.main()
