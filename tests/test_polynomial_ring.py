import random
import unittest
from dataclasses import FrozenInstanceError
from fractions import Fraction

from app.polynomial_ring import (
    SUPPORTED_RING_TYPES,
    CoefficientProfile,
    coefficient_profiles,
    raw_product_multiplicity,
    reduction_targets,
    ring_polynomial,
    validate_ring,
)


RING_TYPES = ("cyclic", "negacyclic", "ntru_prime")
MODULUS_LOW_TERMS = {
    "cyclic": {0: -1},
    "negacyclic": {0: 1},
    "ntru_prime": {0: -1, 1: -1},
}


def modulus_coefficients(n, ring_type):
    coefficients = [0] * (n + 1)
    coefficients[n] = 1
    for degree, value in MODULUS_LOW_TERMS[ring_type].items():
        coefficients[degree] = value
    return coefficients


def integer_polynomial_remainder(polynomial, monic_modulus):
    if not monic_modulus or monic_modulus[-1] != 1:
        raise ValueError("test modulus must be monic")

    modulus_degree = len(monic_modulus) - 1
    remainder = list(polynomial)
    while len(remainder) > modulus_degree:
        leading = remainder[-1]
        shift = len(remainder) - 1 - modulus_degree
        if leading:
            for degree, coefficient in enumerate(monic_modulus):
                remainder[shift + degree] -= leading * coefficient
        if remainder[-1] != 0:
            raise AssertionError("long division did not cancel the leading term")
        remainder.pop()

    return remainder + [0] * (modulus_degree - len(remainder))


def reference_targets(raw_degree, n, ring_type):
    monomial = [0] * (2 * n - 1)
    monomial[raw_degree] = 1
    remainder = integer_polynomial_remainder(
        monomial,
        modulus_coefficients(n, ring_type),
    )
    return tuple(
        (degree, coefficient)
        for degree, coefficient in enumerate(remainder)
        if coefficient
    )


def reference_product(left, right, ring_type):
    n = len(left)
    raw = [0] * (2 * n - 1)
    for left_degree, left_value in enumerate(left):
        for right_degree, right_value in enumerate(right):
            raw[left_degree + right_degree] += left_value * right_value
    return integer_polynomial_remainder(raw, modulus_coefficients(n, ring_type))


def product_using_targets(left, right, ring_type):
    n = len(left)
    raw = [0] * (2 * n - 1)
    for left_degree, left_value in enumerate(left):
        for right_degree, right_value in enumerate(right):
            raw[left_degree + right_degree] += left_value * right_value

    result = [0] * n
    for raw_degree, value in enumerate(raw):
        for output, sign in reduction_targets(raw_degree, n, ring_type):
            result[output] += sign * value
    return result


def reference_profiles(n, ring_type):
    positive = [0] * n
    negative = [0] * n
    for left_degree in range(n):
        for right_degree in range(n):
            raw_degree = left_degree + right_degree
            for output, coefficient in reference_targets(raw_degree, n, ring_type):
                if coefficient > 0:
                    positive[output] += coefficient
                elif coefficient < 0:
                    negative[output] -= coefficient
    return tuple(zip(positive, negative))


def valid_dimensions(ring_type):
    return range(2 if ring_type == "ntru_prime" else 1, 7)


class PolynomialRingTests(unittest.TestCase):
    def test_supported_ring_types_are_immutable_and_names_are_exact(self):
        self.assertIsInstance(SUPPORTED_RING_TYPES, frozenset)
        self.assertEqual(
            SUPPORTED_RING_TYPES,
            frozenset({"cyclic", "negacyclic", "ntru_prime"}),
        )
        with self.assertRaises(AttributeError):
            SUPPORTED_RING_TYPES.add("other")

        self.assertEqual(ring_polynomial(7, "cyclic"), "x^7 - 1")
        self.assertEqual(ring_polynomial(7, "negacyclic"), "x^7 + 1")
        self.assertEqual(ring_polynomial(7, "ntru_prime"), "x^7 - x - 1")

    def test_coefficient_profile_is_frozen(self):
        profile = CoefficientProfile(positive_terms=2, negative_terms=1)

        with self.assertRaises(FrozenInstanceError):
            profile.positive_terms = 3

    def test_reduction_targets_match_generic_remainder_for_every_small_degree(self):
        for ring_type in RING_TYPES:
            for n in valid_dimensions(ring_type):
                for raw_degree in range(2 * n - 1):
                    with self.subTest(n=n, ring_type=ring_type, raw_degree=raw_degree):
                        targets = reduction_targets(raw_degree, n, ring_type)
                        self.assertEqual(targets, reference_targets(raw_degree, n, ring_type))
                        self.assertTrue(all(0 <= output < n for output, _ in targets))

    def test_reduction_targets_reconstruct_deterministic_and_random_products(self):
        random_source = random.Random(20260718)
        for ring_type in RING_TYPES:
            for n in valid_dimensions(ring_type):
                cases = [
                    (
                        [(-1) ** i * (i + 1) for i in range(n)],
                        [((i * i + 2) % 7) - 3 for i in range(n)],
                    )
                ]
                cases.extend(
                    (
                        [random_source.randint(-5, 5) for _ in range(n)],
                        [random_source.randint(-5, 5) for _ in range(n)],
                    )
                    for _ in range(8)
                )

                for case_index, (left, right) in enumerate(cases):
                    with self.subTest(
                        n=n,
                        ring_type=ring_type,
                        case_index=case_index,
                    ):
                        self.assertEqual(
                            product_using_targets(left, right, ring_type),
                            reference_product(left, right, ring_type),
                        )

    def test_raw_product_multiplicity_counts_coefficient_pairs(self):
        for n in range(1, 7):
            for raw_degree in range(2 * n - 1):
                expected = sum(
                    left_degree + right_degree == raw_degree
                    for left_degree in range(n)
                    for right_degree in range(n)
                )
                with self.subTest(n=n, raw_degree=raw_degree):
                    self.assertEqual(raw_product_multiplicity(raw_degree, n), expected)

    def test_profiles_match_generic_remainder_pair_enumeration(self):
        for ring_type in RING_TYPES:
            for n in valid_dimensions(ring_type):
                actual = tuple(
                    (profile.positive_terms, profile.negative_terms)
                    for profile in coefficient_profiles(n, ring_type)
                )
                with self.subTest(n=n, ring_type=ring_type):
                    self.assertEqual(actual, reference_profiles(n, ring_type))

    def test_exact_dimension_four_profiles(self):
        cyclic = coefficient_profiles(4, "cyclic")
        negacyclic = coefficient_profiles(4, "negacyclic")
        ntru_prime = coefficient_profiles(4, "ntru_prime")

        self.assertEqual(
            [(profile.positive_terms, profile.negative_terms) for profile in cyclic],
            [(4, 0)] * 4,
        )
        self.assertEqual(
            [(profile.positive_terms, profile.negative_terms) for profile in negacyclic],
            [(1, 3), (2, 2), (3, 1), (4, 0)],
        )
        self.assertEqual(
            [(profile.positive_terms, profile.negative_terms) for profile in ntru_prime],
            [(4, 0), (7, 0), (6, 0), (5, 0)],
        )

    def test_unknown_and_malformed_ring_types_are_rejected(self):
        for ring_type in ("unknown", "", None, 1, True, Fraction(1, 1), ["cyclic"]):
            for name, operation in (
                ("validate_ring", lambda ring_type=ring_type: validate_ring(4, ring_type)),
                ("ring_polynomial", lambda ring_type=ring_type: ring_polynomial(4, ring_type)),
                (
                    "reduction_targets",
                    lambda ring_type=ring_type: reduction_targets(0, 4, ring_type),
                ),
                (
                    "coefficient_profiles",
                    lambda ring_type=ring_type: coefficient_profiles(4, ring_type),
                ),
            ):
                with (
                    self.subTest(name=name, ring_type=ring_type),
                    self.assertRaisesRegex(ValueError, "^ring_type "),
                ):
                    operation()

    def test_non_integer_dimensions_are_rejected(self):
        for n in (True, False, 1.0, Fraction(1, 1), "1", None):
            for name, operation in (
                ("validate_ring", lambda n=n: validate_ring(n, "cyclic")),
                ("ring_polynomial", lambda n=n: ring_polynomial(n, "cyclic")),
                ("reduction_targets", lambda n=n: reduction_targets(0, n, "cyclic")),
                ("raw_product_multiplicity", lambda n=n: raw_product_multiplicity(0, n)),
                ("coefficient_profiles", lambda n=n: coefficient_profiles(n, "cyclic")),
            ):
                with (
                    self.subTest(name=name, n=n),
                    self.assertRaisesRegex(ValueError, "^n "),
                ):
                    operation()

    def test_nonpositive_dimensions_are_rejected(self):
        for n in (0, -1):
            for name, operation in (
                ("validate_ring", lambda n=n: validate_ring(n, "cyclic")),
                ("ring_polynomial", lambda n=n: ring_polynomial(n, "cyclic")),
                ("reduction_targets", lambda n=n: reduction_targets(0, n, "cyclic")),
                ("raw_product_multiplicity", lambda n=n: raw_product_multiplicity(0, n)),
                ("coefficient_profiles", lambda n=n: coefficient_profiles(n, "cyclic")),
            ):
                with (
                    self.subTest(name=name, n=n),
                    self.assertRaisesRegex(ValueError, "^n "),
                ):
                    operation()

    def test_ntru_prime_requires_dimension_at_least_two(self):
        for name, operation in (
            ("validate_ring", lambda: validate_ring(1, "ntru_prime")),
            ("ring_polynomial", lambda: ring_polynomial(1, "ntru_prime")),
            ("reduction_targets", lambda: reduction_targets(0, 1, "ntru_prime")),
            ("coefficient_profiles", lambda: coefficient_profiles(1, "ntru_prime")),
        ):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    ValueError,
                    "^n must be at least 2 for ntru_prime[.]$",
                ),
            ):
                operation()

        self.assertEqual(ring_polynomial(1, "cyclic"), "x^1 - 1")
        self.assertEqual(ring_polynomial(1, "negacyclic"), "x^1 + 1")

    def test_non_integer_and_out_of_range_raw_degrees_are_rejected(self):
        invalid_degrees = (
            True,
            False,
            1.0,
            Fraction(1, 1),
            "1",
            None,
            -1,
            7,
        )
        for raw_degree in invalid_degrees:
            for name, operation in (
                (
                    "reduction_targets",
                    lambda raw_degree=raw_degree: reduction_targets(raw_degree, 4, "cyclic"),
                ),
                (
                    "raw_product_multiplicity",
                    lambda raw_degree=raw_degree: raw_product_multiplicity(raw_degree, 4),
                ),
            ):
                with (
                    self.subTest(name=name, raw_degree=raw_degree),
                    self.assertRaisesRegex(ValueError, "^raw_degree "),
                ):
                    operation()


if __name__ == "__main__":
    unittest.main()
