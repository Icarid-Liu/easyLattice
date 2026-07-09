import unittest
from unittest.mock import patch

from app.config import AppConfig, EstimatorConfig
from app.ntru_search import parse_ntru_request, recommend_ntru, run_ntru_estimator


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

    def test_ntru_matrix_variant_maps_to_estimator_matrix_type(self):
        result = recommend_ntru(
            {
                "targetSecurity": 128,
                "hardProblemCategory": "ntru",
                "hardProblemVariant": "matrix",
                "ringFamily": "power2",
                "useEstimator": False,
            }
        )

        self.assertEqual(result["recommendation"]["ring"]["ntru_type"], "matrix")

    def test_ntru_estimator_timeout_allows_five_minute_live_runs(self):
        request = parse_ntru_request({"estimatorTimeout": 999})

        self.assertEqual(request.estimator_timeout, 300)

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

    def test_remote_estimator_is_used_when_configured(self):
        result = recommend_ntru(
            {
                "targetSecurity": 128,
                "ringFamily": "power2",
                "useEstimator": False,
            }
        )
        candidate = result["recommendation"]
        remote_result = {"ok": True, "modes": {"classical": {}}}
        config = AppConfig(
            estimator=EstimatorConfig(
                remote_url="https://example-estimator.hf.space",
                remote_timeout_seconds=300,
                remote_poll_interval_seconds=1.0,
            )
        )

        with patch("app.ntru_search.load_config", return_value=config):
            with patch("app.ntru_search.estimate_remotely", return_value=remote_result) as remote:
                self.assertIs(run_ntru_estimator(candidate, 45), remote_result)

        remote.assert_called_once()
        _, kwargs = remote.call_args
        self.assertEqual(kwargs["base_url"], "https://example-estimator.hf.space")
        self.assertEqual(kwargs["timeout_seconds"], 300)
        self.assertEqual(kwargs["payload"]["problem"], "ntru")
        self.assertEqual(kwargs["payload"]["ntru_type"], candidate["ring"]["ntru_type"])
        self.assertIn("secret_distribution", kwargs["payload"])
        self.assertIn("error_distribution", kwargs["payload"])


if __name__ == "__main__":
    unittest.main()
