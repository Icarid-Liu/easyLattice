from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_RING_TYPES = {"cyclic", "negacyclic", "ntru_prime"}


@dataclass(frozen=True)
class CoefficientProfile:
    positive_terms: int
    negative_terms: int


def validate_ring(n: int, ring_type: str) -> None:
    if n < 1:
        raise ValueError("n must be at least 1.")
    if ring_type not in SUPPORTED_RING_TYPES:
        raise ValueError("ring_type must be one of cyclic, negacyclic, ntru_prime.")


def ring_polynomial(n: int, ring_type: str) -> str:
    validate_ring(n, ring_type)
    if ring_type == "cyclic":
        return f"x^{n} - 1"
    if ring_type == "negacyclic":
        return f"x^{n} + 1"
    return f"x^{n} - x - 1"


def reduction_targets(
    raw_degree: int,
    n: int,
    ring_type: str,
) -> tuple[tuple[int, int], ...]:
    validate_ring(n, ring_type)
    _validate_raw_degree(raw_degree, n)
    if raw_degree < n:
        return ((raw_degree, 1),)

    output = raw_degree - n
    if ring_type == "cyclic":
        return ((output, 1),)
    if ring_type == "negacyclic":
        return ((output, -1),)
    return ((output, 1), (output + 1, 1))


def raw_product_multiplicity(raw_degree: int, n: int) -> int:
    if n < 1:
        raise ValueError("n must be at least 1.")
    _validate_raw_degree(raw_degree, n)
    if raw_degree < n:
        return raw_degree + 1
    return 2 * n - 1 - raw_degree


def coefficient_profiles(n: int, ring_type: str) -> tuple[CoefficientProfile, ...]:
    validate_ring(n, ring_type)
    positive = [0] * n
    negative = [0] * n
    for raw_degree in range(2 * n - 1):
        multiplicity = raw_product_multiplicity(raw_degree, n)
        for output, sign in reduction_targets(raw_degree, n, ring_type):
            if sign == 1:
                positive[output] += multiplicity
            else:
                negative[output] += multiplicity

    return tuple(
        CoefficientProfile(positive[index], negative[index])
        for index in range(n)
    )


def _validate_raw_degree(raw_degree: int, n: int) -> None:
    if raw_degree < 0 or raw_degree > 2 * n - 2:
        raise ValueError("raw_degree must be between 0 and 2*n-2.")
