from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any


@dataclass(frozen=True)
class CompressionNoiseProfile:
    q: int
    p: int
    mean: float
    variance: float
    stddev: float
    support: list[int]
    density: float
    mean_shift: int


def compression_noise_profile(q: int, p: int) -> CompressionNoiseProfile:
    """Return the centered compression-noise moment profile for modulus q -> p."""
    validate_compression_parameters(q, p)
    total = q
    raw_sum = 0
    raw_square_sum = 0
    raw_min = 0
    raw_max = 0

    for level in range(p):
        low = ceil_div(level * q, p)
        high = ceil_div((level + 1) * q, p) - 1
        if high < low:
            continue
        reconstruction = (level * q) // p
        count = high - low + 1
        value_sum = arithmetic_sum(low, high)
        value_square_sum = square_sum(high) - square_sum(low - 1)
        raw_sum += count * reconstruction - value_sum
        raw_square_sum += count * reconstruction * reconstruction
        raw_square_sum -= 2 * reconstruction * value_sum
        raw_square_sum += value_square_sum
        raw_min = min(raw_min, reconstruction - high)
        raw_max = max(raw_max, reconstruction - low)

    raw_mean = Fraction(raw_sum, total)
    mean_shift = int(round(raw_mean))
    shifted_mean = raw_mean - mean_shift
    variance = Fraction(raw_square_sum, total) - raw_mean * raw_mean
    support = [raw_min - mean_shift, raw_max - mean_shift]
    zero_count = raw_value_count(q=q, p=p, value=mean_shift)
    density = 1.0 - (zero_count / total)
    return CompressionNoiseProfile(
        q=q,
        p=p,
        mean=float(shifted_mean),
        variance=float(variance),
        stddev=math.sqrt(max(0.0, float(variance))),
        support=support,
        density=density,
        mean_shift=mean_shift,
    )


def compression_noise_pdf(q: int, p: int) -> dict[int, Fraction]:
    """Build the exact centered PDF for small validation cases."""
    validate_compression_parameters(q, p)
    counts: dict[int, int] = {}
    for value in compression_noise_values(q, p):
        counts[value] = counts.get(value, 0) + 1
    mean = sum(Fraction(value * count, q) for value, count in counts.items())
    mean_shift = int(round(mean))
    shifted: dict[int, Fraction] = {}
    for value, count in counts.items():
        shifted[value - mean_shift] = shifted.get(value - mean_shift, Fraction(0)) + Fraction(count, q)
    return shifted


def compression_noise_values(q: int, p: int) -> list[int]:
    validate_compression_parameters(q, p)
    values = []
    for vi in range(q):
        level = ((vi * p) // q) % p
        compressed = (level * q) // p
        values.append(balanced_mod(compressed - vi, q))
    return values


def compression_noise_estimator_distribution(ND: Any, estimator: dict[str, Any], n: int):
    bounds = estimator.get("bounds", [0, 0])
    return ND.NoiseDistribution(
        n=n,
        mean=float(estimator.get("mean", 0.0)),
        stddev=float(estimator["stddev"]),
        bounds=(int(bounds[0]), int(bounds[1])),
        _density=float(estimator.get("density", 1.0)),
    )


def validate_compression_parameters(q: int, p: int) -> None:
    if q < 2:
        raise ValueError("q must be at least 2.")
    if p < 2:
        raise ValueError("compression modulus p must be at least 2.")
    if p >= q:
        raise ValueError("compression modulus p must be smaller than q.")


def raw_value_count(q: int, p: int, value: int) -> int:
    count = 0
    for level in range(p):
        low = ceil_div(level * q, p)
        high = ceil_div((level + 1) * q, p) - 1
        reconstruction = (level * q) // p
        vi = reconstruction - value
        if low <= vi <= high:
            count += 1
    return count


def balanced_mod(value: int, q: int) -> int:
    residue = value % q
    complement = (-residue) % q
    return residue if residue < complement else -complement


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def arithmetic_sum(low: int, high: int) -> int:
    return (low + high) * (high - low + 1) // 2


def square_sum(n: int) -> int:
    if n <= 0:
        return 0
    return n * (n + 1) * (2 * n + 1) // 6
