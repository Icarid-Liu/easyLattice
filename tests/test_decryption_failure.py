import itertools
import json
import unittest
from decimal import (
    Decimal,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_DOWN,
    getcontext,
    localcontext,
    setcontext,
)
from unittest import mock

import app.decryption_failure as dfr
from app.decryption_failure import (
    calculate_decryption_failure,
    convolve_pmfs,
    kyber_nearest_compression_pmf,
    normalized_pmf,
    pmf_from_distribution,
    ring_product_coefficient_pmfs,
    scaled_ring_products,
)


ZERO = {"type": "custom_pmf", "pmf": {"0": "1"}}
ONE = {"type": "custom_pmf", "pmf": {"1": "1"}}
BERNOULLI = {"type": "custom_pmf", "pmf": {"0": "0.5", "1": "0.5"}}


def ntru_product_payload(ring_type):
    return {
        "type": "ntru",
        "ringType": ring_type,
        "n": 3,
        "p0": 1,
        "p1": 0,
        "p2": 0,
        "p3": 0,
        "delta": 1,
        "g": BERNOULLI,
        "s": BERNOULLI,
        "f": ZERO,
        "e": ZERO,
        "m": ZERO,
    }


def independent_product_term_signs(n, ring_type, output_index):
    signs = []
    for left_degree in range(n):
        for right_degree in range(n):
            raw_degree = left_degree + right_degree
            if raw_degree < n:
                targets = ((raw_degree, 1),)
            elif ring_type == "cyclic":
                targets = ((raw_degree - n, 1),)
            elif ring_type == "negacyclic":
                targets = ((raw_degree - n, -1),)
            elif ring_type == "ntru_prime":
                targets = ((raw_degree - n, 1), (raw_degree - n + 1, 1))
            else:
                raise AssertionError("test reference requires a supported ring")
            signs.extend(sign for output, sign in targets if output == output_index)
    return signs


def brute_force_bernoulli_product_coefficient(n, ring_type, output_index):
    probabilities = {}
    signs = independent_product_term_signs(n, ring_type, output_index)
    product_probability = Decimal("0.25")
    zero_probability = Decimal("0.75")
    for outcomes in itertools.product((0, 1), repeat=len(signs)):
        value = Decimal(sum(sign * outcome for sign, outcome in zip(signs, outcomes)))
        probability = Decimal(1)
        for outcome in outcomes:
            probability *= product_probability if outcome else zero_probability
        probabilities[value] = probabilities.get(value, Decimal(0)) + probability
    return probabilities


class DecryptionFailureTests(unittest.TestCase):
    def test_ring_product_pmfs_and_failures_match_independent_brute_force(self):
        bernoulli = normalized_pmf({Decimal(0): Decimal(1), Decimal(1): Decimal(1)})
        for ring_type in ("cyclic", "negacyclic", "ntru_prime"):
            with self.subTest(ring_type=ring_type):
                coefficient_pmfs = ring_product_coefficient_pmfs(
                    bernoulli,
                    bernoulli,
                    2,
                    ring_type,
                )
                expected_pmfs = tuple(
                    brute_force_bernoulli_product_coefficient(2, ring_type, index)
                    for index in range(2)
                )
                self.assertEqual(
                    tuple(pmf.probabilities for pmf in coefficient_pmfs),
                    expected_pmfs,
                )

                result = calculate_decryption_failure(
                    ntru_product_payload(ring_type) | {"n": 2, "delta": 0}
                )
                expected_failures = [
                    sum(
                        probability
                        for value, probability in probabilities.items()
                        if abs(value) > 0
                    )
                    for probabilities in expected_pmfs
                ]
                actual_failures = [
                    Decimal(value)
                    for value in result["coefficient_dfr"]["failure_probabilities"]
                ]
                self.assertEqual(actual_failures, expected_failures)
                self.assertEqual(
                    Decimal(result["single_coefficient_failure_probability"]),
                    max(expected_failures),
                )
                self.assertEqual(
                    Decimal(result["vector_failure_probability_before_ecc"]),
                    min(Decimal(1), sum(expected_failures, Decimal(0))),
                )

    def test_ring_products_reuse_profiles_and_zero_scale_is_exact(self):
        bernoulli = normalized_pmf({Decimal(0): Decimal(1), Decimal(1): Decimal(1)})
        cyclic = ring_product_coefficient_pmfs(bernoulli, bernoulli, 4, "cyclic")
        self.assertTrue(all(pmf is cyclic[0] for pmf in cyclic))

        scaled_cyclic = scaled_ring_products(
            Decimal(2),
            bernoulli,
            bernoulli,
            4,
            "cyclic",
        )
        self.assertTrue(all(pmf is scaled_cyclic[0] for pmf in scaled_cyclic))

        zero_scaled = scaled_ring_products(
            Decimal(0),
            bernoulli,
            bernoulli,
            3,
            "negacyclic",
        )
        self.assertEqual(
            tuple(pmf.probabilities for pmf in zero_scaled),
            ({Decimal(0): Decimal(1)},) * 3,
        )
        self.assertTrue(all(pmf is zero_scaled[0] for pmf in zero_scaled))
        with self.assertRaisesRegex(ValueError, "n must be at least 2 for ntru_prime"):
            scaled_ring_products(Decimal(0), bernoulli, bernoulli, 1, "ntru_prime")

    def test_cyclic_ntru_reuses_each_distinct_coefficient_computation(self):
        payload = ntru_product_payload("cyclic") | {
            "n": 509,
            "delta": 509,
        }
        with (
            mock.patch.object(dfr, "add_pmfs", wraps=dfr.add_pmfs) as add_pmfs,
            mock.patch.object(
                dfr,
                "coefficient_failure_probability",
                wraps=dfr.coefficient_failure_probability,
            ) as failure_probability,
        ):
            result = calculate_decryption_failure(payload)

        self.assertEqual(add_pmfs.call_count, 2)
        self.assertEqual(failure_probability.call_count, 1)
        self.assertEqual(result["coefficient_dfr"]["distinct_profiles"], 1)
        self.assertEqual(len(result["coefficient_dfr"]["failure_probabilities"]), 509)
        self.assertEqual(set(result["coefficient_dfr"]["failure_probabilities"]), {"0"})

    def test_lwe_aggregation_does_not_scale_work_with_vector_dimension(self):
        with mock.patch.object(
            dfr,
            "coefficient_failure_probability",
            wraps=dfr.coefficient_failure_probability,
        ) as failure_probability:
            result = calculate_decryption_failure({
                "type": "lwe",
                "m": 1,
                "n": dfr.MAX_LWE_DIMENSION,
                "delta": 0,
                "s": ZERO,
                "e": ZERO,
                "e1": ZERO,
                "r": ZERO,
                "e2": ZERO,
                "ec1": ZERO,
                "ec2": ZERO,
            })

        self.assertEqual(failure_probability.call_count, 1)
        self.assertEqual(result["dimensions"]["n"], dfr.MAX_LWE_DIMENSION)
        self.assertNotIn("coefficient_dfr", result)

    def test_dimension_limits_reject_oversized_and_boolean_inputs(self):
        oversized = 10_000_000
        cases = (
            (
                {"type": "ntru", "n": oversized},
                rf"^n must not exceed {dfr.MAX_NTRU_DIMENSION} \(MAX_NTRU_DIMENSION\)[.]$",
            ),
            (
                {"type": "lwe", "m": oversized, "n": 1},
                rf"^m must not exceed {dfr.MAX_LWE_DIMENSION} \(MAX_LWE_DIMENSION\)[.]$",
            ),
            (
                {"type": "lwe", "m": 1, "n": oversized},
                rf"^n must not exceed {dfr.MAX_LWE_DIMENSION} \(MAX_LWE_DIMENSION\)[.]$",
            ),
        )
        for payload, message in cases:
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, message):
                calculate_decryption_failure(payload)

        for payload, dimension in (
            ({"type": "ntru", "n": True}, "n"),
            ({"type": "lwe", "m": True, "n": 1}, "m"),
            ({"type": "lwe", "m": 1, "n": True}, "n"),
        ):
            with (
                self.subTest(payload=payload),
                self.assertRaisesRegex(ValueError, rf"^{dimension} "),
            ):
                calculate_decryption_failure(payload)

    def test_dimension_limit_boundaries_accept_cheap_zero_models(self):
        ntru_result = calculate_decryption_failure(
            ntru_product_payload("cyclic")
            | {
                "n": dfr.MAX_NTRU_DIMENSION,
                "p0": 0,
                "g": ZERO,
                "s": ZERO,
            }
        )
        self.assertEqual(ntru_result["dimensions"]["n"], dfr.MAX_NTRU_DIMENSION)
        self.assertEqual(
            len(ntru_result["coefficient_dfr"]["failure_probabilities"]),
            dfr.MAX_NTRU_DIMENSION,
        )

        lwe_result = calculate_decryption_failure({
            "type": "lwe",
            "m": dfr.MAX_LWE_DIMENSION,
            "n": dfr.MAX_LWE_DIMENSION,
            "delta": 0,
            "s": ZERO,
            "e": ZERO,
            "e1": ZERO,
            "r": ZERO,
            "e2": ZERO,
            "ec1": ZERO,
            "ec2": ZERO,
        })
        self.assertEqual(lwe_result["dimensions"], {
            "m": dfr.MAX_LWE_DIMENSION,
            "n": dfr.MAX_LWE_DIMENSION,
        })

    def test_hostile_integer_text_rejects_before_decimal_or_int_conversion(self):
        exponent = "1e10000000"
        huge_digits = "9" * 100_000
        operations = (
            lambda: dfr.positive_int(exponent, "dimension"),
            lambda: dfr.nonnegative_int(exponent, "weight"),
            lambda: dfr.bounded_dimension(
                exponent,
                "n",
                dfr.MAX_NTRU_DIMENSION,
                "MAX_NTRU_DIMENSION",
            ),
            lambda: calculate_decryption_failure({"type": "ntru", "precisionBits": exponent}),
            lambda: calculate_decryption_failure({"type": "ntru", "tailBits": exponent}),
            lambda: calculate_decryption_failure({"type": "ntru", "n": exponent}),
            lambda: calculate_decryption_failure({"type": "ntru", "n": huge_digits}),
            lambda: calculate_decryption_failure({"type": "lwe", "m": exponent, "n": 1}),
            lambda: pmf_from_distribution(
                {"type": "centered_binomial", "eta": exponent},
                default_dimension=1,
                tail_bits=128,
                label="noise",
            ),
            lambda: pmf_from_distribution(
                {
                    "type": "sparse_ternary",
                    "plus_weight": exponent,
                    "minus_weight": 0,
                    "dimension": 3,
                },
                default_dimension=3,
                tail_bits=128,
                label="secret",
            ),
            lambda: pmf_from_distribution(
                {"type": "uniform_mod", "modulus": exponent},
                default_dimension=1,
                tail_bits=128,
                label="uniform",
            ),
            lambda: pmf_from_distribution(
                {"type": "kyber_nearest_compression", "q": 3329, "d": exponent},
                default_dimension=1,
                tail_bits=128,
                label="compression",
            ),
        )
        with mock.patch.object(
            dfr,
            "scalar",
            side_effect=AssertionError("integer parser delegated to Decimal scalar parsing"),
        ):
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises(ValueError):
                    operation()
        with self.assertRaisesRegex(
            ValueError,
            "^n must be a non-negative integer[.]$",
        ):
            calculate_decryption_failure({"type": "ntru", "n": exponent})
        with mock.patch.object(
            dfr,
            "Decimal",
            side_effect=AssertionError("hostile exponent reached Decimal construction"),
        ):
            with self.assertRaisesRegex(ValueError, "non-negative integer"):
                dfr.safe_nonnegative_integer(
                    exponent,
                    "n",
                    dfr.MAX_NTRU_DIMENSION,
                    "MAX_NTRU_DIMENSION",
                )

    def test_hostile_scalar_text_is_rejected_across_all_untrusted_decimal_fields(self):
        huge_positive_exponent = "1e10000000"
        huge_negative_exponent = "1e-10000000"
        huge_digits = "9" * 100_000
        base = ntru_product_payload("cyclic") | {
            "n": 1,
            "p0": 0,
            "delta": 1,
        }

        def coefficient(value):
            return base | {"p0": value}

        def delta(value):
            return base | {"delta": value}

        def gaussian_mean(value):
            return base | {
                "e": {"type": "discrete_gaussian", "stddev": "1", "mean": value},
            }

        def gaussian_stddev(value):
            return base | {
                "e": {"type": "discrete_gaussian", "stddev": value, "mean": "0"},
            }

        def pmf_support(value):
            return base | {
                "e": {"type": "custom_pmf", "pmf": {value: "1"}},
            }

        def pmf_probability(value):
            return base | {
                "e": {"type": "custom_pmf", "pmf": {"0": value}},
            }

        fields = (
            ("p0", coefficient),
            ("delta", delta),
            ("e.mean", gaussian_mean),
            ("e.stddev", gaussian_stddev),
            ("e.pmf value", pmf_support),
            ("e.pmf", pmf_probability),
        )
        for label, payload_for in fields:
            for hostile in (
                huge_positive_exponent,
                huge_negative_exponent,
                huge_digits,
            ):
                with (
                    self.subTest(label=label, hostile=hostile[:24]),
                    self.assertRaisesRegex(ValueError, label.replace(".", r"\.") + ".*supported"),
                ):
                    calculate_decryption_failure(payload_for(hostile))

    def test_sqrt2_special_value_obeys_original_text_length_limit(self):
        padded = " " * dfr.MAX_NUMERIC_TEXT_LENGTH + "sqrt(2)"
        with self.assertRaisesRegex(ValueError, "p1.*supported"):
            calculate_decryption_failure(
                ntru_product_payload("cyclic") | {"n": 1, "p1": padded}
            )

    def test_decimal_objects_obey_digit_exponent_and_magnitude_limits(self):
        huge_coefficient = Decimal("1." + "0" * 100_000)
        hostile_values = (
            Decimal("1e10000000"),
            Decimal("1e-10000000"),
            huge_coefficient,
        )
        base = ntru_product_payload("cyclic") | {"n": 1, "p0": 0, "delta": 1}

        def coefficient(value):
            return base | {"p0": value}

        def delta(value):
            return base | {"delta": value}

        def gaussian_mean(value):
            return base | {
                "e": {"type": "discrete_gaussian", "stddev": "1", "mean": value},
            }

        def gaussian_stddev(value):
            return base | {
                "e": {"type": "discrete_gaussian", "stddev": value, "mean": "0"},
            }

        def pmf_support(value):
            return base | {
                "e": {"type": "custom_pmf", "pmf": {value: Decimal(1)}},
            }

        def pmf_probability(value):
            return base | {
                "e": {"type": "custom_pmf", "pmf": {"0": value}},
            }

        fields = (
            ("p0", coefficient),
            ("delta", delta),
            ("e.mean", gaussian_mean),
            ("e.stddev", gaussian_stddev),
            ("e.pmf value", pmf_support),
            ("e.pmf", pmf_probability),
        )
        for label, payload_for in fields:
            for hostile in hostile_values:
                with (
                    self.subTest(label=label, hostile=hostile.adjusted()),
                    self.assertRaisesRegex(
                        ValueError,
                        label.replace(".", r"\.") + ".*supported",
                    ),
                ):
                    calculate_decryption_failure(payload_for(hostile))

    def test_bounded_scalar_forms_remain_supported(self):
        result = calculate_decryption_failure(
            ntru_product_payload("cyclic")
            | {
                "n": 1,
                "p0": "  +1.25e1  ",
                "p1": "sqrt(2)",
                "delta": Decimal("1.5e2"),
                "g": {"type": "custom_pmf", "pmf": {"-5e-1": "2.5e-1", "1.25": ".75"}},
                "s": ONE,
            }
        )

        self.assertEqual(Decimal(result["coefficients"]["p0"]), Decimal("12.5"))
        sqrt_two = Decimal(result["coefficients"]["p1"])
        self.assertLess(abs(sqrt_two * sqrt_two - Decimal(2)), Decimal("1e-25"))
        self.assertEqual(Decimal(result["delta"]), Decimal("150"))

    def test_decimal_arithmetic_failures_are_normalized_to_value_error(self):
        for decimal_error in (Overflow(), InvalidOperation()):
            with (
                self.subTest(decimal_error=type(decimal_error).__name__),
                mock.patch.object(dfr, "calculate_ntru", side_effect=decimal_error),
                self.assertRaisesRegex(
                    ValueError,
                    "DFR numeric calculation exceeds the supported Decimal range",
                ),
            ):
                calculate_decryption_failure({"type": "ntru"})

    def test_exact_integer_json_values_remain_supported(self):
        result = calculate_decryption_failure(
            ntru_product_payload("cyclic")
            | {
                "n": "  +3.0  ",
                "precisionBits": "5.12e2",
                "tailBits": Decimal("128.0"),
            }
        )
        self.assertEqual(result["dimensions"]["n"], 3)
        self.assertEqual(result["precision_bits"], 512)

        centered = pmf_from_distribution(
            {"type": "centered_binomial", "eta": "2.0"},
            default_dimension=1,
            tail_bits=128,
            label="noise",
        )
        sparse = pmf_from_distribution(
            {
                "type": "sparse_ternary",
                "plus_weight": " +1e0 ",
                "minus_weight": Decimal("1.0"),
                "dimension": "4.0",
            },
            default_dimension=4,
            tail_bits=128,
            label="secret",
        )
        compression = pmf_from_distribution(
            {
                "type": "kyber_nearest_compression",
                "q": "3.329e3",
                "d": "1e1",
            },
            default_dimension=1,
            tail_bits=128,
            label="compression",
        )
        self.assertEqual(len(centered.probabilities), 5)
        self.assertEqual(sparse.probabilities[Decimal(0)], Decimal("0.5"))
        self.assertGreater(len(compression.probabilities), 1)

    def test_uniform_bounds_accept_bounded_scientific_and_fractional_notation(self):
        scientific = pmf_from_distribution(
            {
                "type": "uniform",
                "lower_bound": "  +1e2 ",
                "upper_bound": "1.025e2",
            },
            default_dimension=1,
            tail_bits=128,
            label="uniform",
        )
        fractional = pmf_from_distribution(
            {
                "type": "uniform",
                "lower_bound": "1.2",
                "upper_bound": Decimal("3.8"),
            },
            default_dimension=1,
            tail_bits=128,
            label="uniform",
        )
        self.assertEqual(set(scientific.probabilities), {
            Decimal(100),
            Decimal(101),
            Decimal(102),
        })
        self.assertEqual(set(fractional.probabilities), {Decimal(2), Decimal(3)})
        limit = dfr.MAX_SAFE_INTEGER_PARAMETER
        self.assertEqual(dfr.floor_int(f"{limit}.9", "upper"), limit)
        self.assertEqual(dfr.ceiling_int(f"-{limit}.9", "lower"), -limit)

    def test_nonintegral_integer_fields_are_rejected(self):
        operations = (
            lambda: calculate_decryption_failure({"type": "ntru", "n": "3.5"}),
            lambda: dfr.positive_int(3.5, "dimension"),
            lambda: dfr.nonnegative_int(Decimal("2.1"), "weight"),
            lambda: pmf_from_distribution(
                {"type": "centered_binomial", "eta": "2.1"},
                default_dimension=1,
                tail_bits=128,
                label="noise",
            ),
        )
        for operation in operations:
            with (
                self.subTest(operation=operation),
                self.assertRaisesRegex(ValueError, "integer"),
            ):
                operation()

    def test_integer_driven_distribution_loops_have_preflight_limits(self):
        with (
            mock.patch.object(
                dfr,
                "compression_noise_pdf",
                side_effect=AssertionError("compression enumeration started"),
            ),
            self.assertRaisesRegex(ValueError, "MAX_PMF_SUPPORT"),
        ):
            pmf_from_distribution(
                {
                    "type": "lwr_floor_compression",
                    "q": dfr.MAX_PMF_SUPPORT + 1,
                    "p": 2,
                },
                default_dimension=1,
                tail_bits=128,
                label="compression",
            )
        with self.assertRaisesRegex(ValueError, "MAX_PMF_SUPPORT"):
            dfr.kyber_nearest_compression_pmf(dfr.MAX_PMF_SUPPORT + 1, 1)
        with self.assertRaisesRegex(ValueError, "MAX_COMPRESSION_BITS"):
            dfr.kyber_nearest_compression_pmf(3329, dfr.MAX_COMPRESSION_BITS + 1)

    def test_expensive_ntru_prime_profiles_reject_before_power_convolution(self):
        for n in (509, 653):
            with (
                self.subTest(n=n),
                mock.patch.object(
                    dfr,
                    "convolve_power",
                    wraps=dfr.convolve_power,
                ) as convolve_power,
                self.assertRaisesRegex(ValueError, "MAX_RING_PROFILE_WORK"),
            ):
                calculate_decryption_failure(
                    ntru_product_payload("ntru_prime")
                    | {"n": n, "delta": n}
                )
            self.assertEqual(convolve_power.call_count, 0)

    def test_ntru_profile_budget_is_cumulative_across_active_terms(self):
        bernoulli = normalized_pmf({Decimal(0): Decimal(1), Decimal(1): Decimal(1)})
        product = dfr.multiply_pmfs(bernoulli, bernoulli)
        profiles = dfr.coefficient_profiles(160, "ntru_prime")
        self.assertLess(dfr.ring_profile_work(product, profiles), dfr.MAX_RING_PROFILE_WORK)

        with (
            mock.patch.object(
                dfr,
                "convolve_power",
                wraps=dfr.convolve_power,
            ) as convolve_power,
            self.assertRaisesRegex(ValueError, "MAX_RING_PROFILE_WORK"),
        ):
            calculate_decryption_failure(
                ntru_product_payload("ntru_prime")
                | {
                    "n": 160,
                    "p1": 1,
                    "f": BERNOULLI,
                    "e": BERNOULLI,
                    "delta": 160,
                }
            )
        self.assertEqual(convolve_power.call_count, 0)

    def test_large_deterministic_ntru_prime_stays_within_profile_budget(self):
        with mock.patch.object(
            dfr,
            "convolve_power",
            wraps=dfr.convolve_power,
        ) as convolve_power:
            result = calculate_decryption_failure(
                ntru_product_payload("ntru_prime")
                | {
                    "n": 653,
                    "g": ONE,
                    "s": ONE,
                    "delta": 1305,
                }
            )

        self.assertGreater(convolve_power.call_count, 0)
        self.assertEqual(result["dimensions"]["n"], 653)
        self.assertEqual(len(result["coefficient_dfr"]["failure_probabilities"]), 653)
        self.assertEqual(set(result["coefficient_dfr"]["failure_probabilities"]), {"0"})

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
        self.assertEqual(result["single_coefficient_semantics"], "identical_coefficient_model")
        self.assertEqual(result["warning_codes"], ["dfr_union_bound"])
        self.assertFalse(result["error_correction"]["included"])
        self.assertEqual(result["error_correction"]["code"], "dfr_ecc_external")

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
        self.assertEqual(result["ring_type"], "cyclic")
        self.assertEqual(result["ring_polynomial"], "x^3 - 1")
        self.assertEqual(result["single_coefficient_semantics"], "worst_coefficient")
        self.assertEqual(
            [Decimal(value) for value in result["coefficient_dfr"]["failure_probabilities"]],
            [Decimal("0.5")] * 3,
        )

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
        self.assertIn("dfr_sparse_fixed_weight_marginal", result["warning_codes"])
        self.assertEqual(result["warning_codes"].count("dfr_sparse_fixed_weight_marginal"), 1)
        self.assertEqual(
            sum("fixed-weight" in warning for warning in result["warnings"]),
            1,
        )

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
        self.assertIn("dfr_gaussian_tail_excluded", result["warning_codes"])

    def test_discrete_gaussian_preflights_exp_work_and_bounds_valid_calls(self):
        decimal_digits = dfr.decimal_digits_for_bits(dfr.DEFAULT_PRECISION_BITS)
        with dfr.dfr_decimal_context(decimal_digits):
            with mock.patch.object(
                dfr,
                "decimal_exp",
                wraps=dfr.decimal_exp,
            ) as decimal_exp:
                with self.assertRaisesRegex(
                    ValueError,
                    "MAX_GAUSSIAN_EXP_WORK",
                ):
                    dfr.discrete_gaussian_pmf(
                        Decimal(dfr.MAX_SCALAR_ABS),
                        Decimal(0),
                        dfr.DEFAULT_TAIL_BITS,
                    )
            self.assertEqual(decimal_exp.call_count, 0)

            plan = dfr.discrete_gaussian_work_plan(
                Decimal(50),
                dfr.DEFAULT_TAIL_BITS,
            )
            self.assertLessEqual(plan.projected_work, dfr.MAX_GAUSSIAN_EXP_WORK)
            self.assertGreater(plan.projected_work, dfr.MAX_GAUSSIAN_EXP_WORK // 2)
            with mock.patch.object(
                dfr,
                "decimal_exp",
                wraps=dfr.decimal_exp,
            ) as decimal_exp:
                gaussian = dfr.discrete_gaussian_pmf(
                    Decimal(50),
                    Decimal(0),
                    dfr.DEFAULT_TAIL_BITS,
                )

            self.assertLessEqual(decimal_exp.call_count, plan.exp_calls_upper_bound)
            self.assertLessEqual(len(gaussian.probabilities), plan.support_width)

        maximum_digits = dfr.decimal_digits_for_bits(dfr.MAX_PRECISION_BITS)
        with dfr.dfr_decimal_context(maximum_digits):
            with mock.patch.object(
                dfr,
                "decimal_exp",
                wraps=dfr.decimal_exp,
            ) as decimal_exp:
                with self.assertRaisesRegex(
                    ValueError,
                    "MAX_GAUSSIAN_EXP_WORK",
                ):
                    dfr.discrete_gaussian_pmf(
                        Decimal(50),
                        Decimal(0),
                        dfr.DEFAULT_TAIL_BITS,
                    )
            self.assertEqual(decimal_exp.call_count, 0)

    def test_ntru_rejects_cumulative_gaussian_work_before_generation(self):
        gaussian = {"type": "discrete_gaussian", "stddev": "15"}
        payload = {
            "type": "ntru",
            "n": 1,
            "p0": 0,
            "p1": 0,
            "p2": 0,
            "p3": 0,
            "delta": 1,
            "g": gaussian,
            "f": gaussian,
            "s": gaussian,
            "e": gaussian,
            "m": gaussian,
        }

        with mock.patch.object(
            dfr,
            "decimal_exp",
            wraps=dfr.decimal_exp,
        ) as decimal_exp:
            with self.assertRaisesRegex(
                ValueError,
                "MAX_GAUSSIAN_REQUEST_WORK",
            ):
                calculate_decryption_failure(payload)

        self.assertEqual(decimal_exp.call_count, 0)

    def test_lwe_rejects_cumulative_gaussian_work_before_generation(self):
        gaussian = {"type": "discrete_gaussian", "stddev": "10"}
        payload = {
            "type": "lwe",
            "m": 1,
            "n": 1,
            "delta": 1,
            "s": gaussian,
            "e": gaussian,
            "e1": gaussian,
            "r": gaussian,
            "e2": gaussian,
            "ec1": gaussian,
            "ec2": gaussian,
        }

        with mock.patch.object(
            dfr,
            "decimal_exp",
            wraps=dfr.decimal_exp,
        ) as decimal_exp:
            with self.assertRaisesRegex(
                ValueError,
                "MAX_GAUSSIAN_REQUEST_WORK",
            ):
                calculate_decryption_failure(payload)

        self.assertEqual(decimal_exp.call_count, 0)

    def test_dfr_decimal_context_isolated_from_caller_context(self):
        payload = {
            "type": "ntru",
            "n": 1,
            "p0": 0,
            "p1": "sqrt(2)",
            "p2": 0,
            "p3": 1,
            "delta": 10,
            "g": ZERO,
            "f": ZERO,
            "s": ZERO,
            "e": {"type": "discrete_gaussian", "stddev": "1.5"},
            "m": ZERO,
            "tailBits": 40,
        }
        expected = calculate_decryption_failure(payload)
        saved = getcontext().copy()
        caller = getcontext()
        try:
            caller.rounding = ROUND_DOWN
            caller.Emax = 9
            caller.Emin = -9
            caller.capitals = 0
            caller.clamp = 1
            caller.traps[Inexact] = True

            actual = calculate_decryption_failure(payload)

            self.assertEqual(actual, expected)
            self.assertEqual(getcontext().rounding, ROUND_DOWN)
            self.assertEqual(getcontext().Emax, 9)
            self.assertEqual(getcontext().Emin, -9)
            self.assertEqual(getcontext().capitals, 0)
            self.assertEqual(getcontext().clamp, 1)
            self.assertTrue(getcontext().traps[Inexact])
        finally:
            setcontext(saved)

    def test_ntru_reports_ring_and_worst_coefficient_metadata(self):
        result = calculate_decryption_failure(ntru_product_payload("negacyclic"))

        self.assertEqual(result["ring_type"], "negacyclic")
        self.assertEqual(result["ring_polynomial"], "x^3 + 1")
        self.assertEqual(result["single_coefficient_semantics"], "worst_coefficient")
        self.assertEqual(result["coefficient_dfr"]["worst_index"], 2)
        self.assertEqual(result["coefficient_dfr"]["distinct_profiles"], 3)
        self.assertEqual(result["error_support"]["size"], 4)
        self.assertEqual(Decimal(result["error_support"]["minimum"]), Decimal(0))
        self.assertEqual(Decimal(result["error_support"]["maximum"]), Decimal(3))
        self.assertEqual(result["coefficient_dfr"]["profiles"], [
            {"positive_terms": 1, "negative_terms": 2},
            {"positive_terms": 2, "negative_terms": 1},
            {"positive_terms": 3, "negative_terms": 0},
        ])

    def test_ntru_prime_vector_union_sums_marginals_and_warns(self):
        result = calculate_decryption_failure(ntru_product_payload("ntru_prime"))
        coefficient_failures = [
            Decimal(value)
            for value in result["coefficient_dfr"]["failure_probabilities"]
        ]

        self.assertEqual(
            Decimal(result["vector_failure_probability_before_ecc"]),
            sum(coefficient_failures, Decimal(0)),
        )
        self.assertEqual(
            Decimal(result["single_coefficient_failure_probability"]),
            max(coefficient_failures),
        )
        self.assertEqual(result["coefficient_dfr"]["worst_index"], 1)
        self.assertIn("ntru_prime_coefficient_marginal", result["warning_codes"])
        self.assertTrue(any(
            "no joint independence claim" in warning
            for warning in result["warnings"]
        ))

    def test_ntru_accepts_snake_case_ring_and_rejects_invalid_rings(self):
        snake_case_payload = ntru_product_payload("negacyclic")
        snake_case_payload["ring_type"] = snake_case_payload.pop("ringType")
        result = calculate_decryption_failure(snake_case_payload)
        self.assertEqual(result["ring_type"], "negacyclic")

        with self.assertRaisesRegex(ValueError, "ring_type"):
            calculate_decryption_failure(ntru_product_payload("ordinary"))
        with self.assertRaisesRegex(ValueError, "n must be at least 2 for ntru_prime"):
            calculate_decryption_failure(ntru_product_payload("ntru_prime") | {"n": 1})

    def test_ntru_dfr_numbers_are_decimal_strings_and_json_safe(self):
        result = calculate_decryption_failure(ntru_product_payload("ntru_prime"))
        decimal_fields = (
            "delta",
            "single_coefficient_dfr_log2",
            "vector_dfr_log2_before_ecc",
            "single_coefficient_failure_probability",
            "vector_failure_probability_before_ecc",
            "tail_probability_upper_bound",
        )
        self.assertTrue(all(isinstance(result[name], str) for name in decimal_fields))
        self.assertTrue(all(
            isinstance(value, str)
            for value in result["coefficient_dfr"]["failure_probabilities"]
        ))
        json.dumps(result, allow_nan=False)

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

    def test_custom_pmf_json_string_rejects_nonfinite_constants(self):
        with self.assertRaisesRegex(
            ValueError,
            "^non-finite JSON constant is not allowed: NaN$",
        ):
            pmf_from_distribution(
                {
                    "type": "custom_pmf",
                    "pmf": '{"0": 1, "1": NaN}',
                },
                default_dimension=1,
                tail_bits=128,
                label="noise",
            )

    def test_custom_pmf_support_rejects_hostile_exponent_before_int_conversion(self):
        hostile = {
            "type": "custom_pmf",
            "pmf": {"1e10000000": "1"},
        }
        with (
            self.assertRaisesRegex(ValueError, "MAX_PMF_ABS_SUPPORT"),
            mock.patch(
                "builtins.int",
                side_effect=AssertionError("hostile support reached int conversion"),
            ),
            mock.patch.object(
                dfr,
                "integer_grid",
                side_effect=AssertionError("hostile support reached integer_grid"),
            ),
        ):
            pmf = pmf_from_distribution(
                hostile,
                default_dimension=1,
                tail_bits=128,
                label="noise",
            )
            convolve_pmfs(pmf, dfr.zero_pmf())

    def test_custom_pmf_support_accepts_bounded_scientific_and_fractional_values(self):
        scientific = pmf_from_distribution(
            {"type": "custom_pmf", "pmf": {"1e2": "1"}},
            default_dimension=1,
            tail_bits=128,
            label="noise",
        )
        fractional = pmf_from_distribution(
            {
                "type": "custom_pmf",
                "pmf": {"-5e-1": "0.25", "1.25": "0.75"},
            },
            default_dimension=1,
            tail_bits=128,
            label="noise",
        )

        self.assertEqual(scientific.probabilities, {Decimal(100): Decimal(1)})
        self.assertEqual(
            fractional.probabilities,
            {Decimal("-0.5"): Decimal("0.25"), Decimal("1.25"): Decimal("0.75")},
        )
        self.assertEqual(dfr.integer_grid(scientific), (100, [Decimal(1)]))
        self.assertIsNone(dfr.integer_grid(fractional))

    def test_integer_grid_rejects_huge_decimal_without_int_conversion(self):
        pmf = dfr.PMF({Decimal("1e10000000"): Decimal(1)})
        with mock.patch(
            "builtins.int",
            side_effect=AssertionError("huge coordinate reached int conversion"),
        ):
            self.assertIsNone(dfr.integer_grid(pmf))

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
