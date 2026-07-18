import unittest
from dataclasses import FrozenInstanceError

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


def reference_targets(raw_degree, n, ring_type):
    if raw_degree < n:
        return ((raw_degree, 1),)
    if ring_type == "cyclic":
        return ((raw_degree - n, 1),)
    if ring_type == "negacyclic":
        return ((raw_degree - n, -1),)
    return ((raw_degree - n, 1), (raw_degree - n + 1, 1))


def reference_product(left, right, ring_type):
    n = len(left)
    result = [0] * n
    for left_degree, left_value in enumerate(left):
        for right_degree, right_value in enumerate(right):
            raw_degree = left_degree + right_degree
            for output, sign in reference_targets(raw_degree, n, ring_type):
                result[output] += sign * left_value * right_value
    return result


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
            for output, sign in reference_targets(raw_degree, n, ring_type):
                if sign == 1:
                    positive[output] += 1
                else:
                    negative[output] += 1
    return tuple(zip(positive, negative))


class PolynomialRingTests(unittest.TestCase):
    def test_supported_ring_types_and_polynomial_names(self):
        self.assertEqual(SUPPORTED_RING_TYPES, {"cyclic", "negacyclic", "ntru_prime"})
        self.assertEqual(ring_polynomial(7, "cyclic"), "x^7 - 1")
        self.assertEqual(ring_polynomial(7, "negacyclic"), "x^7 + 1")
        self.assertEqual(ring_polynomial(7, "ntru_prime"), "x^7 - x - 1")

    def test_coefficient_profile_is_frozen(self):
        profile = CoefficientProfile(positive_terms=2, negative_terms=1)

        with self.assertRaises(FrozenInstanceError):
            profile.positive_terms = 3

    def test_reduction_targets_match_reference_for_every_small_degree(self):
        for n in range(1, 7):
            for ring_type in RING_TYPES:
                for raw_degree in range(2 * n - 1):
                    with self.subTest(n=n, ring_type=ring_type, raw_degree=raw_degree):
                        targets = reduction_targets(raw_degree, n, ring_type)
                        self.assertEqual(targets, reference_targets(raw_degree, n, ring_type))
                        self.assertTrue(all(0 <= output < n for output, _ in targets))

    def test_reduction_targets_reconstruct_all_three_products(self):
        for n in range(1, 7):
            left = [(-1) ** i * (i + 1) for i in range(n)]
            right = [((i * i + 2) % 7) - 3 for i in range(n)]
            for ring_type in RING_TYPES:
                with self.subTest(n=n, ring_type=ring_type):
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

    def test_profiles_match_independent_pair_enumeration(self):
        for n in range(1, 7):
            for ring_type in RING_TYPES:
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
        self.assertEqual(sum(profile.positive_terms for profile in ntru_prime), 22)

    def test_unknown_ring_type_is_rejected(self):
        for operation in (
            lambda: validate_ring(4, "unknown"),
            lambda: ring_polynomial(4, "unknown"),
            lambda: reduction_targets(0, 4, "unknown"),
            lambda: coefficient_profiles(4, "unknown"),
        ):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "ring_type"):
                operation()

    def test_nonpositive_dimensions_are_rejected(self):
        for n in (0, -1):
            for operation in (
                lambda n=n: validate_ring(n, "cyclic"),
                lambda n=n: ring_polynomial(n, "cyclic"),
                lambda n=n: reduction_targets(0, n, "cyclic"),
                lambda n=n: raw_product_multiplicity(0, n),
                lambda n=n: coefficient_profiles(n, "cyclic"),
            ):
                with self.subTest(n=n, operation=operation), self.assertRaisesRegex(ValueError, "n"):
                    operation()

    def test_bad_raw_degrees_are_rejected(self):
        for n in (1, 4):
            for raw_degree in (-1, 2 * n - 1):
                for operation in (
                    lambda raw_degree=raw_degree, n=n: reduction_targets(raw_degree, n, "cyclic"),
                    lambda raw_degree=raw_degree, n=n: raw_product_multiplicity(raw_degree, n),
                ):
                    with (
                        self.subTest(n=n, raw_degree=raw_degree, operation=operation),
                        self.assertRaisesRegex(ValueError, "raw_degree"),
                    ):
                        operation()


if __name__ == "__main__":
    unittest.main()
