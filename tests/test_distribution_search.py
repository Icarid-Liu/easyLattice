import unittest

from app.distribution_search import (
    DistributionRequest,
    centered_binomial_component,
    compose_distribution,
    distribution_order_key,
    enumerate_distribution_candidates,
    parse_distribution_request,
    sparse_ternary_component,
)
from app.ntru_search import parse_ntru_request
from app.parameter_search import parse_request


class DistributionSearchTests(unittest.TestCase):
    def test_distribution_defaults_are_pure_and_bounded(self):
        secret = parse_distribution_request({}, "secret")
        self.assertEqual((secret.mode, secret.selector, secret.max_components), ("pure", "auto", 3))

    def test_composition_sums_variance_and_support(self):
        cbd = centered_binomial_component(2)
        sparse = sparse_ternary_component(2, 2, 512)
        result = compose_distribution([cbd, sparse])
        self.assertEqual(result["family"], "composite")
        self.assertEqual(result["component_count"], 2)
        self.assertEqual(result["estimator"]["type"], "composite_moment")
        self.assertAlmostEqual(result["variance"], cbd["variance"] + sparse["variance"])
        self.assertEqual(result["support"], [-3, 3])

    def test_pure_composition_keeps_component_estimator(self):
        result = compose_distribution([centered_binomial_component(2)])
        self.assertEqual(result["family"], "pure")
        self.assertEqual(result["component_count"], 1)
        self.assertEqual(result["estimator"]["type"], "centered_binomial")

    def test_component_limit_is_one_through_six(self):
        for value in (0, 7, "three", 3.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_distribution_request(
                    {
                        "secretDistributionMode": "combination",
                        "maxDistributionComponents": value,
                    },
                    "secret",
                )

    def test_mode_and_lwr_error_validation(self):
        request = parse_distribution_request(
            {
                "errorDistributionMode": "combination",
                "maxDistributionComponents": 6,
            },
            "error",
        )
        self.assertEqual((request.mode, request.max_components), ("combination", 6))
        with self.assertRaises(ValueError):
            parse_distribution_request(
                {"errorDistributionMode": "combination"},
                "error",
                lwr_error=True,
            )

    def test_search_requests_expose_independent_module_controls(self):
        raw = {
            "secretDistribution": "centered_binomial",
            "secretDistributionMode": "combination",
            "errorDistribution": "sparse_ternary",
            "errorDistributionMode": "pure",
            "maxDistributionComponents": 4,
        }
        rlwe = parse_request(raw)
        ntru = parse_ntru_request(raw)
        for request in (rlwe, ntru):
            self.assertEqual(request.secret_distribution, "centered_binomial")
            self.assertEqual(request.error_distribution, "sparse_ternary")
            self.assertEqual(request.secret_distribution_mode, "combination")
            self.assertEqual(request.error_distribution_mode, "pure")
            self.assertEqual(request.max_distribution_components, 4)

    def test_lwr_request_rejects_error_combination(self):
        with self.assertRaises(ValueError):
            parse_request(
                {
                    "hardProblemVariant": "RLWR",
                    "errorDistributionMode": "combination",
                }
            )

    def test_combination_enumeration_is_bounded_and_ordered(self):
        request = DistributionRequest(mode="combination", selector="centered_binomial", max_components=2)
        candidates = list(enumerate_distribution_candidates(512, request))
        self.assertTrue(candidates)
        self.assertTrue(all(item["component_count"] <= 2 for item in candidates))
        self.assertEqual(candidates[0]["family"], "pure")
        self.assertEqual(candidates[-1]["family"], "composite")
        self.assertEqual(
            [distribution_order_key(item) for item in candidates],
            sorted(distribution_order_key(item) for item in candidates),
        )


if __name__ == "__main__":
    unittest.main()
