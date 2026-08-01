"""Regression fixtures for the narrow pure-distribution searches.

The default tests exercise request generation without Sage.  Set
``EASYLATTICE_RUN_SAGE_TESTS=1`` to run the two live estimator smoke tests on a
machine with the configured standard/enhanced profiles.
"""

import os
import unittest

from app.config import load_config
from app.estimator_process import estimator_profile_for
from app.ntru_search import recommend_ntru
from app.parameter_search import recommend_rlwe


FIXTURE = {
    "targetSecurity": 128,
    "securityModel": "classical",
    "redCostModel": "matzov",
    "ringFamily": "power2",
    "minN": 512,
    "maxN": 512,
    "minQBits": 7,
    "maxQBits": 12,
    "nttScalePower": 1,
    "secretDistribution": "centered_binomial",
    "errorDistribution": "centered_binomial",
    "secretDistributionMode": "pure",
    "errorDistributionMode": "pure",
    "maxDistributionComponents": 3,
}


class AdaptiveSearchFixtureTests(unittest.TestCase):
    def test_ntru_fixture_defaults_to_pure_and_standard_profile(self):
        request = dict(FIXTURE, problem="ntru", hardProblemCategory="ntru", hardProblemVariant="ring")
        result = recommend_ntru(request)
        self.assertEqual(result["request"]["secret_distribution_mode"], "pure")
        self.assertEqual(result["request"]["error_distribution_mode"], "pure")
        self.assertEqual(estimator_profile_for("ntru", "ring"), "standard")
        self.assertEqual(result["recommendation"]["ring"]["n"], 512)
        self.assertEqual(result["recommendation"]["distribution"]["mode"], {"secret": "pure", "error": "pure"})

    def test_rlwe_fixture_defaults_to_pure_and_enhanced_profile(self):
        request = dict(FIXTURE, problem="rlwe", hardProblemCategory="lwe", hardProblemVariant="rlwe")
        result = recommend_rlwe(request)
        self.assertEqual(result["request"]["secret_distribution_mode"], "pure")
        self.assertEqual(result["request"]["error_distribution_mode"], "pure")
        self.assertEqual(estimator_profile_for("lwe", "rlwe"), "enhanced")
        self.assertEqual(result["recommendation"]["ring"]["n"], 512)
        self.assertEqual(result["recommendation"]["distribution"]["mode"], {"secret": "pure", "error": "pure"})


@unittest.skipUnless(
    os.environ.get("EASYLATTICE_RUN_SAGE_TESTS") == "1",
    "set EASYLATTICE_RUN_SAGE_TESTS=1 to run live Sage estimator smoke tests",
)
class LiveEstimatorSearchTests(unittest.TestCase):
    def test_ntru_pure_fixture_live(self):
        result = recommend_ntru(
            dict(FIXTURE, problem="ntru", hardProblemCategory="ntru", hardProblemVariant="ring", useEstimator=True),
            config=load_config(),
        )
        self.assertIn(result["validation"]["search_status"], {"target_met", "no_feasible_candidate", "cancelled"})
        self.assertEqual(result["request"]["secret_distribution_mode"], "pure")

    def test_rlwe_pure_fixture_live(self):
        result = recommend_rlwe(
            dict(FIXTURE, problem="rlwe", hardProblemCategory="lwe", hardProblemVariant="rlwe", useEstimator=True),
            config=load_config(),
        )
        self.assertIn(result["validation"]["search_status"], {"target_met", "no_feasible_candidate", "cancelled"})
        self.assertEqual(result["request"]["error_distribution_mode"], "pure")


if __name__ == "__main__":
    unittest.main()
