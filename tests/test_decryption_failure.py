import unittest
from decimal import Decimal, localcontext

from app.decryption_failure import (
    calculate_decryption_failure,
    convolve_pmfs,
    kyber_nearest_compression_pmf,
    normalized_pmf,
    pmf_from_distribution,
)


ZERO = {"type": "custom_pmf", "pmf": {"0": "1"}}


class DecryptionFailureTests(unittest.TestCase):
    def test_lwe_reports_single_and_vector_dfr(self):
        result = calculate_decryption_failure({
            "type": "lwe",
            "m": 1,
            "n": 2,
            "delta": 0,
            "s": ZERO,
            "e": ZERO,
            "e1": ZERO,
            "r": ZERO,
            "ec1": ZERO,
            "ec2": ZERO,
            "e2": {"type": "custom_pmf", "pmf": {"-1": "0.125", "0": "0.75", "1": "0.125"}},
        })

        self.assertEqual(Decimal(result["single_coefficient_failure_probability"]), Decimal("0.25"))
        self.assertEqual(Decimal(result["vector_failure_probability_before_ecc"]), Decimal("0.5"))
        self.assertEqual(result["single_coefficient_dfr_log2"], "-2.000000000000000000000000")
        self.assertEqual(result["vector_dfr_log2_before_ecc"], "-1.000000000000000000000000")
        self.assertEqual(result["vector_aggregation"], "union_bound")
        self.assertNotIn("single_coefficient_dfr", result)
        self.assertEqual(result["precision_bits"], 512)
        self.assertEqual(result["success_condition"], "|E| <= Delta")
        self.assertFalse(result["error_correction"]["included"])

    def test_ntru_uses_p3_error_term_and_vector_dimension(self):
        result = calculate_decryption_failure({
            "type": "ntru",
            "n": 3,
            "p0": 0,
            "p1": 0,
            "p2": 0,
            "p3": 1,
            "delta": 0,
            "g": ZERO,
            "f": ZERO,
            "s": ZERO,
            "m": ZERO,
            "e": {"type": "custom_pmf", "pmf": {"0": "0.5", "1": "0.5"}},
        })

        self.assertEqual(Decimal(result["single_coefficient_failure_probability"]), Decimal("0.5"))
        self.assertEqual(Decimal(result["vector_failure_probability_before_ecc"]), Decimal("1"))

    def test_boundary_at_delta_is_successful(self):
        payload = {
            "type": "ntru",
            "n": 1,
            "p0": 0,
            "p1": 0,
            "p2": 0,
            "p3": 1,
            "g": ZERO,
            "f": ZERO,
            "s": ZERO,
            "m": ZERO,
            "e": {"type": "custom_pmf", "pmf": {"1": "1"}},
        }

        self.assertEqual(Decimal(calculate_decryption_failure(payload | {"delta": 1})["single_coefficient_failure_probability"]), 0)
        self.assertEqual(Decimal(calculate_decryption_failure(payload | {"delta": 0})["single_coefficient_failure_probability"]), 1)

    def test_sparse_ternary_uses_estimator_style_fixed_weight_marginal(self):
        sparse = {
            "estimator": {
                "type": "sparse_ternary_fixed_weight",
                "plus_weight": 1,
                "minus_weight": 1,
            }
        }
        result = calculate_decryption_failure({
            "type": "ntru",
            "n": 4,
            "p0": 0,
            "p1": 0,
            "p2": 0,
            "p3": 1,
            "delta": 0,
            "g": ZERO,
            "f": ZERO,
            "s": ZERO,
            "m": ZERO,
            "e": sparse,
        })

        self.assertEqual(Decimal(result["single_coefficient_failure_probability"]), Decimal("0.5"))
        self.assertTrue(any("fixed-weight" in warning for warning in result["warnings"]))

    def test_kyber_nearest_compression_is_distinct_and_centered(self):
        with localcontext() as context:
            context.prec = 80
            pdf = kyber_nearest_compression_pmf(5, 1).probabilities
            lwr = pmf_from_distribution(
                {"type": "lwr_floor_compression", "q": 5, "p": 2},
                default_dimension=1,
                tail_bits=128,
                label="ec",
            ).probabilities

        self.assertEqual(pdf, {
            Decimal(-1): Decimal("0.2"),
            Decimal(0): Decimal("0.4"),
            Decimal(1): Decimal("0.4"),
        })
        self.assertNotEqual(pdf, lwr)

    def test_nev_sqrt2_coefficient_is_accepted(self):
        result = calculate_decryption_failure({
            "type": "ntru",
            "n": 1,
            "p0": 0,
            "p1": "sqrt(2)",
            "p2": 0,
            "p3": 0,
            "delta": 1,
            "g": ZERO,
            "f": {"type": "custom_pmf", "pmf": {"1": "1"}},
            "s": ZERO,
            "e": {"type": "custom_pmf", "pmf": {"1": "1"}},
            "m": ZERO,
        })

        self.assertEqual(Decimal(result["single_coefficient_failure_probability"]), 1)

    def test_discrete_gaussian_reports_tail_bound(self):
        gaussian = {"type": "discrete_gaussian", "stddev": "1.5"}
        result = calculate_decryption_failure({
            "type": "ntru",
            "n": 1,
            "p0": 0,
            "p1": 0,
            "p2": 0,
            "p3": 1,
            "delta": 10,
            "g": ZERO,
            "f": ZERO,
            "s": ZERO,
            "m": ZERO,
            "e": gaussian,
            "tailBits": 40,
        })

        self.assertLess(Decimal(result["tail_probability_upper_bound"]), Decimal(2) ** Decimal(-40))

    def test_custom_pmf_and_noise_distribution_validation(self):
        invalid = {"type": "custom_pmf", "pmf": {"0": "0.2", "1": "0.2"}}
        payload = {
            "type": "lwe",
            "m": 1,
            "n": 1,
            "delta": 0,
            "s": ZERO,
            "e": ZERO,
            "e1": ZERO,
            "r": ZERO,
            "e2": ZERO,
            "ec1": ZERO,
            "ec2": invalid,
        }
        with self.assertRaisesRegex(ValueError, "must sum to 1"):
            calculate_decryption_failure(payload)

        with self.assertRaisesRegex(ValueError, "custom_pmf"):
            calculate_decryption_failure(payload | {"ec2": {"type": "noise_distribution"}})

    def test_karatsuba_convolution_matches_small_hand_distribution(self):
        left = normalized_pmf({Decimal(value): Decimal(1) for value in range(65)})
        right = normalized_pmf({Decimal(value): Decimal(1) for value in range(65)})
        result = convolve_pmfs(left, right).probabilities

        self.assertEqual(len(result), 129)
        self.assertAlmostEqual(float(sum(result.values())), 1.0)
        self.assertAlmostEqual(float(result[Decimal(0)]), 1 / 65**2)
        self.assertAlmostEqual(float(result[Decimal(64)]), 65 / 65**2)

    def test_kyber_512_shape_uses_two_compression_distributions(self):
        cbd3 = {"type": "centered_binomial", "eta": 3}
        cbd2 = {"type": "centered_binomial", "eta": 2}
        result = calculate_decryption_failure({
            "type": "lwe",
            "m": 512,
            "n": 256,
            "delta": 832,
            "s": cbd3,
            "e": cbd3,
            "e1": cbd2,
            "r": cbd3,
            "e2": cbd2,
            "ec1": {"type": "kyber_nearest_compression", "q": 3329, "d": 10},
            "ec2": {"type": "kyber_nearest_compression", "q": 3329, "d": 4},
        })

        self.assertEqual(result["formula"], "((e1 + ec1)*s)_m + (e*r)_m + e2 + ec2")
        self.assertEqual(result["dimensions"], {"m": 512, "n": 256})
        self.assertGreater(result["error_support"]["size"], 10_000)
        self.assertNotEqual(result["single_coefficient_dfr_log2"], "-Infinity")


if __name__ == "__main__":
    unittest.main()
