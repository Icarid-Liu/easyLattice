from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, getcontext, localcontext
from fractions import Fraction
from math import comb
from typing import Any

from .compression_noise import balanced_mod, compression_noise_pdf
from .polynomial_ring import CoefficientProfile, coefficient_profiles, ring_polynomial


DEFAULT_PRECISION_BITS = 512
DEFAULT_TAIL_BITS = 128
MAX_PRECISION_BITS = 4096
MAX_TAIL_BITS = 1024
# Request limits cover supported presets while bounding audit and convolution work.
MAX_NTRU_DIMENSION = 4096
MAX_LWE_DIMENSION = 65_536
MAX_SAFE_INTEGER_PARAMETER = 2**53 - 1
# PMF coordinates stay within a safe exact-integer magnitude before grid conversion.
MAX_PMF_ABS_SUPPORT = MAX_SAFE_INTEGER_PARAMETER
MAX_INTEGER_TEXT_LENGTH = 64
MAX_NUMERIC_EXPONENT_ABS = 1_000
MAX_COMPRESSION_BITS = 63
MAX_RING_PROFILE_WORK = 1_000_000
MAX_PMF_SUPPORT = 50_000
MAX_PAIR_PRODUCTS = 30_000_000

DECIMAL_NUMBER_PATTERN = re.compile(
    r"^[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))"
    r"(?:[eE](?P<exponent>[+-]?[0-9]+))?$"
)


@dataclass(frozen=True)
class PMF:
    probabilities: dict[Decimal, Decimal]
    tail_bound: Decimal = Decimal(0)
    warnings: tuple[str, ...] = field(default_factory=tuple)


def calculate_decryption_failure(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Calculate pre-error-correction DFR for the supported NTRU or LWE form."""
    if not isinstance(raw, dict):
        raise ValueError("Request body must be a JSON object.")

    precision_bits = bounded_positive_int(
        raw.get("precisionBits", raw.get("precision_bits", DEFAULT_PRECISION_BITS)),
        "precisionBits",
        MAX_PRECISION_BITS,
        "MAX_PRECISION_BITS",
    )
    tail_bits = bounded_positive_int(
        raw.get("tailBits", raw.get("tail_bits", DEFAULT_TAIL_BITS)),
        "tailBits",
        MAX_TAIL_BITS,
        "MAX_TAIL_BITS",
    )

    decimal_digits = decimal_digits_for_bits(precision_bits)
    with localcontext() as context:
        context.prec = decimal_digits
        kind = str(raw.get("type", "")).strip().lower()
        if kind == "ntru":
            return calculate_ntru(raw, precision_bits, decimal_digits, tail_bits)
        if kind == "lwe":
            return calculate_lwe(raw, precision_bits, decimal_digits, tail_bits)
        raise ValueError("type must be one of ntru, lwe.")


def calculate_ntru(
    raw: dict[str, Any],
    precision_bits: int,
    decimal_digits: int,
    tail_bits: int,
) -> dict[str, Any]:
    n = bounded_dimension(
        raw.get("n"),
        "n",
        MAX_NTRU_DIMENSION,
        "MAX_NTRU_DIMENSION",
    )
    ring_type = raw.get("ringType", raw.get("ring_type", "cyclic"))
    if isinstance(ring_type, str):
        ring_type = ring_type.strip().lower()
    ring_description = ring_polynomial(n, ring_type)
    profiles = coefficient_profiles(n, ring_type)
    delta = nonnegative_scalar(raw.get("delta", raw.get("Delta")), "delta")
    coefficients = {
        name: nonnegative_scalar(raw.get(name), name)
        for name in ("p0", "p1", "p2", "p3")
    }
    distributions = {
        name: pmf_from_distribution(raw.get(name), default_dimension=n, tail_bits=tail_bits, label=name)
        for name in ("g", "f", "s", "e", "m")
    }

    product_specs = (
        (coefficients["p0"], distributions["g"], distributions["s"]),
        (coefficients["p1"], distributions["f"], distributions["e"]),
        (coefficients["p2"], distributions["f"], distributions["m"]),
    )
    base_products: list[tuple[Decimal, PMF] | None] = []
    profile_work = 0
    for scale, left, right in product_specs:
        if scale == 0:
            base_products.append(None)
            continue
        product = multiply_pmfs(left, right)
        profile_work = accumulated_ring_profile_work(profile_work, product, profiles)
        base_products.append((scale, product))

    zero_products = None
    product_term_list = []
    for base_product in base_products:
        if base_product is None:
            if zero_products is None:
                zero = zero_pmf()
                zero_products = (zero,) * n
            product_term_list.append(zero_products)
        else:
            scale, product = base_product
            product_term_list.append(
                scaled_ring_products_from_product(scale, product, profiles)
            )
    product_terms = tuple(product_term_list)
    direct_error = scale_pmf(distributions["e"], coefficients["p3"])
    error_cache: dict[tuple[int, ...], PMF] = {}
    coefficient_error_list = []
    for index in range(n):
        inputs = tuple(term[index] for term in product_terms) + (direct_error,)
        key = tuple(id(item) for item in inputs)
        if key not in error_cache:
            error_cache[key] = add_pmfs(*inputs)
        coefficient_error_list.append(error_cache[key])
    coefficient_errors = tuple(coefficient_error_list)
    payload = result_payload(
        kind="ntru",
        formula="p0*(g*s)_n + p1*(f*e)_n + p2*(f*m)_n + p3*e",
        dimensions={"n": n},
        delta=delta,
        error=coefficient_errors[0],
        vector_dimension=n,
        precision_bits=precision_bits,
        decimal_digits=decimal_digits,
        tail_bits=tail_bits,
        distributions=distributions,
        coefficients=coefficients,
        coefficient_errors=coefficient_errors,
        distinct_profiles=len({
            (profile.positive_terms, profile.negative_terms)
            for profile in profiles
        }),
    )
    payload["ring_type"] = ring_type
    payload["ring_polynomial"] = ring_description
    payload["coefficient_dfr"]["profiles"] = [
        {
            "positive_terms": profile.positive_terms,
            "negative_terms": profile.negative_terms,
        }
        for profile in profiles
    ]
    if ring_type == "ntru_prime":
        payload["warning_codes"] = list(dict.fromkeys(
            payload["warning_codes"] + ["ntru_prime_coefficient_marginal"]
        ))
        payload["warnings"] = list(dict.fromkeys(payload["warnings"] + [
            "NTRU Prime ring products use a coefficient-marginal approximation; "
            "the vector union bound makes no joint independence claim.",
        ]))
    return payload


def calculate_lwe(
    raw: dict[str, Any],
    precision_bits: int,
    decimal_digits: int,
    tail_bits: int,
) -> dict[str, Any]:
    m = bounded_dimension(
        raw.get("m"),
        "m",
        MAX_LWE_DIMENSION,
        "MAX_LWE_DIMENSION",
    )
    n = bounded_dimension(
        raw.get("n"),
        "n",
        MAX_LWE_DIMENSION,
        "MAX_LWE_DIMENSION",
    )
    delta = nonnegative_scalar(raw.get("delta", raw.get("Delta")), "delta")
    defaults = {
        "s": m,
        "e": m,
        "e1": m,
        "r": m,
        "e2": n,
        "ec1": m,
        "ec2": n,
    }
    distributions = {
        name: pmf_from_distribution(raw.get(name), default_dimension=dimension, tail_bits=tail_bits, label=name)
        for name, dimension in defaults.items()
    }

    e1_ec1 = add_pmfs(distributions["e1"], distributions["ec1"])
    term_e1s = convolve_power(multiply_pmfs(e1_ec1, distributions["s"]), m)
    term_er = convolve_power(multiply_pmfs(distributions["e"], distributions["r"]), m)
    error = add_pmfs(term_e1s, term_er, distributions["e2"], distributions["ec2"])
    return result_payload(
        kind="lwe",
        formula="((e1 + ec1)*s)_m + (e*r)_m + e2 + ec2",
        dimensions={"m": m, "n": n},
        delta=delta,
        error=error,
        vector_dimension=n,
        precision_bits=precision_bits,
        decimal_digits=decimal_digits,
        tail_bits=tail_bits,
        distributions=distributions,
    )


def result_payload(
    *,
    kind: str,
    formula: str,
    dimensions: dict[str, int],
    delta: Decimal,
    error: PMF,
    vector_dimension: int,
    precision_bits: int,
    decimal_digits: int,
    tail_bits: int,
    distributions: dict[str, PMF],
    coefficients: dict[str, Decimal] | None = None,
    coefficient_errors: tuple[PMF, ...] | None = None,
    distinct_profiles: int | None = None,
) -> dict[str, Any]:
    coefficient_specific = coefficient_errors is not None
    failures: list[Decimal] | None = None
    if coefficient_errors is None:
        worst_index = 0
        single_failure = coefficient_failure_probability(error, delta)
        vector_failure = aggregate_vector_failure(single_failure, vector_dimension)
        reported_error = error
        tail_bound = error.tail_bound
        warnings = list(error.warnings)
    else:
        if len(coefficient_errors) != vector_dimension:
            raise ValueError("coefficient_errors must match the vector dimension.")
        analysis_cache: dict[int, tuple[Decimal, Decimal, tuple[str, ...]]] = {}
        failures = []
        for item in coefficient_errors:
            key = id(item)
            if key not in analysis_cache:
                analysis_cache[key] = (
                    coefficient_failure_probability(item, delta),
                    item.tail_bound,
                    item.warnings,
                )
            failures.append(analysis_cache[key][0])
        worst_index = max(range(len(failures)), key=failures.__getitem__)
        single_failure = failures[worst_index]
        vector_failure = min(Decimal(1), sum(failures, Decimal(0)))
        reported_error = coefficient_errors[worst_index]
        tail_bound = max(analysis[1] for analysis in analysis_cache.values())
        warnings = list(dict.fromkeys(
            warning
            for analysis in analysis_cache.values()
            for warning in analysis[2]
        ))
    aggregation_warning = "Vector DFR uses a union bound and does not assume independent output coefficients."
    if aggregation_warning not in warnings:
        warnings.append(aggregation_warning)
    warning_codes = ["dfr_union_bound"]
    if tail_bound:
        tail_warning = "Reported probabilities exclude bounded discrete-Gaussian tails."
        if tail_warning not in warnings:
            warnings.append(tail_warning)
        warning_codes.append("dfr_gaussian_tail_excluded")
    if any("fixed-weight" in warning for warning in warnings):
        warning_codes.append("dfr_sparse_fixed_weight_marginal")

    payload: dict[str, Any] = {
        "ok": True,
        "type": kind,
        "formula": formula,
        "success_condition": "|E| <= Delta",
        "dimensions": dimensions,
        "delta": decimal_text(delta),
        "precision_bits": precision_bits,
        "precision_decimal_digits": decimal_digits,
        "tail_bits": tail_bits,
        "single_coefficient_dfr_log2": log2_text(single_failure),
        "vector_dfr_log2_before_ecc": log2_text(vector_failure),
        "single_coefficient_failure_probability": decimal_text(single_failure),
        "vector_failure_probability_before_ecc": decimal_text(vector_failure),
        "single_coefficient_semantics": (
            "worst_coefficient"
            if coefficient_specific
            else "identical_coefficient_model"
        ),
        "vector_aggregation": "union_bound",
        "tail_probability_upper_bound": decimal_text(tail_bound),
        "error_support": {
            "size": len(reported_error.probabilities),
            "minimum": decimal_text(min(reported_error.probabilities)),
            "maximum": decimal_text(max(reported_error.probabilities)),
        },
        "distributions": {
            name: pmf_summary(pmf)
            for name, pmf in distributions.items()
        },
        "warnings": warnings,
        "warning_codes": list(dict.fromkeys(warning_codes)),
        "error_correction": {
            "included": False,
            "code": "dfr_ecc_external",
            "note": "Apply a scheme-specific error-correction calculation outside this module.",
        },
    }
    if coefficients is not None:
        payload["coefficients"] = {name: decimal_text(value) for name, value in coefficients.items()}
    if coefficient_specific:
        assert failures is not None
        payload["coefficient_dfr"] = {
            "worst_index": worst_index,
            "distinct_profiles": distinct_profiles,
            "failure_probabilities": [decimal_text(failure) for failure in failures],
        }
    return payload


def pmf_from_distribution(
    raw: Any,
    *,
    default_dimension: int,
    tail_bits: int,
    label: str,
) -> PMF:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a distribution object.")
    spec = raw.get("estimator") if isinstance(raw.get("estimator"), dict) else raw
    if not isinstance(spec, dict):
        raise ValueError(f"{label} must contain a distribution object.")

    distribution_type = str(spec.get("type", spec.get("family", ""))).strip().lower()
    aliases = {
        "sparse_ternary_fixed_weight": "sparse_ternary",
        "compression_noise": "lwr_floor_compression",
        "lwr_compression": "lwr_floor_compression",
        "kyber_compression": "kyber_nearest_compression",
    }
    distribution_type = aliases.get(distribution_type, distribution_type)
    if distribution_type == "centered_binomial":
        eta = nonnegative_int(value_of(spec, "eta"), f"{label}.eta")
        ensure_support_size(2 * eta + 1)
        weights = {Decimal(i): Decimal(comb(2 * eta, eta + i)) for i in range(-eta, eta + 1)}
        return normalized_pmf(weights)
    if distribution_type == "discrete_gaussian":
        stddev = positive_scalar(value_of(spec, "stddev"), f"{label}.stddev")
        mean = scalar(value_of(spec, "mean", default=0), f"{label}.mean")
        return discrete_gaussian_pmf(stddev, mean, tail_bits)
    if distribution_type == "uniform":
        lower = ceiling_int(value_of(spec, "lower_bound", "lower", "a"), f"{label}.lower_bound")
        upper = floor_int(value_of(spec, "upper_bound", "upper", "b"), f"{label}.upper_bound")
        if upper < lower:
            raise ValueError(f"{label} upper bound must be at least its lower bound.")
        return uniform_pmf(lower, upper)
    if distribution_type == "uniform_mod":
        modulus = positive_int(value_of(spec, "modulus", "q"), f"{label}.modulus")
        lower = -(modulus // 2)
        return uniform_pmf(lower, lower + modulus - 1)
    if distribution_type == "t_uniform":
        exponent = nonnegative_int(value_of(spec, "b", "exponent"), f"{label}.b")
        return t_uniform_pmf(exponent)
    if distribution_type == "sparse_ternary":
        plus = nonnegative_int(value_of(spec, "plus_weight", "plus", "p"), f"{label}.plus_weight")
        minus = nonnegative_int(value_of(spec, "minus_weight", "minus", "m", default=plus), f"{label}.minus_weight")
        dimension = positive_int(value_of(spec, "dimension", "n", default=default_dimension), f"{label}.dimension")
        return sparse_ternary_pmf(plus, minus, dimension)
    if distribution_type == "sparse_binary":
        weight = nonnegative_int(value_of(spec, "weight", "hw", "p"), f"{label}.weight")
        dimension = positive_int(value_of(spec, "dimension", "n", default=default_dimension), f"{label}.dimension")
        return sparse_ternary_pmf(weight, 0, dimension)
    if distribution_type == "binary":
        return normalized_pmf({Decimal(0): Decimal(1), Decimal(1): Decimal(1)})
    if distribution_type == "ternary":
        return normalized_pmf({Decimal(-1): Decimal(1), Decimal(0): Decimal(1), Decimal(1): Decimal(1)})
    if distribution_type == "lwr_floor_compression":
        q = positive_int(value_of(spec, "q", "modulus"), f"{label}.q")
        p = positive_int(value_of(spec, "p", "compression_modulus"), f"{label}.p")
        ensure_exact_domain_size(q, f"{label}.q")
        return pmf_from_fraction_map(compression_noise_pdf(q, p))
    if distribution_type == "kyber_nearest_compression":
        q = positive_int(value_of(spec, "q", "modulus"), f"{label}.q")
        bits = nonnegative_int(value_of(spec, "d", "bits", "compression_bits"), f"{label}.d")
        return kyber_nearest_compression_pmf(q, bits)
    if distribution_type == "custom_pmf":
        return custom_pmf(value_of(spec, "pmf", "distribution"), label, tail_bits)
    if distribution_type == "noise_distribution":
        raise ValueError(f"{label} NoiseDistribution only has moments; use custom_pmf instead.")
    raise ValueError(f"Unsupported distribution type for {label}: {distribution_type or 'missing type'}.")


def uniform_pmf(lower: int, upper: int) -> PMF:
    ensure_support_size(upper - lower + 1)
    return normalized_pmf({Decimal(value): Decimal(1) for value in range(lower, upper + 1)})


def t_uniform_pmf(exponent: int) -> PMF:
    maximum_exponent = (((MAX_PMF_SUPPORT - 1) // 2).bit_length() - 1)
    if exponent > maximum_exponent:
        ensure_support_size(MAX_PMF_SUPPORT + 1)
    radius = 2**exponent
    ensure_support_size(2 * radius + 1)
    endpoint_weight = Decimal(1) / (Decimal(2) ** (exponent + 2))
    middle_weight = Decimal(1) / (Decimal(2) ** (exponent + 1))
    probabilities = {
        Decimal(value): endpoint_weight if abs(value) == radius else middle_weight
        for value in range(-radius, radius + 1)
    }
    return normalized_pmf(probabilities)


def sparse_ternary_pmf(plus: int, minus: int, dimension: int) -> PMF:
    if plus + minus > dimension:
        raise ValueError("Sparse ternary weights must not exceed their dimension.")
    probabilities = {
        Decimal(-1): Decimal(minus) / Decimal(dimension),
        Decimal(0): Decimal(dimension - plus - minus) / Decimal(dimension),
        Decimal(1): Decimal(plus) / Decimal(dimension),
    }
    return normalized_pmf(
        probabilities,
        warnings=(
            "Sparse ternary uses its single-coefficient marginal and ignores fixed-weight correlation.",
        ),
    )


def discrete_gaussian_pmf(stddev: Decimal, mean: Decimal, tail_bits: int) -> PMF:
    target_tail = Decimal(2) ** Decimal(-tail_bits)
    radius = 0
    while discrete_gaussian_tail_bound(stddev, radius) > target_tail:
        radius += 1
        ensure_support_size(2 * radius + 1)

    weights = {
        mean + Decimal(offset): (-(Decimal(offset * offset) / (Decimal(2) * stddev * stddev))).exp()
        for offset in range(-radius, radius + 1)
    }
    return normalized_pmf(weights, tail_bound=discrete_gaussian_tail_bound(stddev, radius))


def discrete_gaussian_tail_bound(stddev: Decimal, radius: int) -> Decimal:
    first_omitted = Decimal(radius + 1)
    exponent = (-(first_omitted * first_omitted) / (Decimal(2) * stddev * stddev)).exp()
    bound = Decimal(2) * exponent * (Decimal(1) + (stddev * stddev / first_omitted))
    return min(Decimal(1), bound)


def custom_pmf(raw: Any, label: str, tail_bits: int) -> PMF:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}.pmf must be valid JSON.") from exc
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{label}.pmf must be a non-empty JSON object.")

    probabilities: dict[Decimal, Decimal] = {}
    for raw_value, raw_probability in raw.items():
        value = bounded_finite_decimal(
            raw_value,
            f"{label}.pmf value",
            MAX_PMF_ABS_SUPPORT,
            "MAX_PMF_ABS_SUPPORT",
        )
        probability = nonnegative_scalar(raw_probability, f"{label}.pmf[{raw_value!r}]")
        probabilities[value] = probabilities.get(value, Decimal(0)) + probability
    total = sum(probabilities.values(), Decimal(0))
    tolerance = Decimal(2) ** Decimal(-min(tail_bits, 128))
    if abs(total - Decimal(1)) > tolerance:
        raise ValueError(f"{label}.pmf probabilities must sum to 1.")
    return normalized_pmf(probabilities)


def kyber_nearest_compression_pmf(q: int, bits: int) -> PMF:
    if q < 2:
        raise ValueError("Compression modulus q must be at least 2.")
    ensure_exact_domain_size(q, "Compression modulus q")
    if bits > MAX_COMPRESSION_BITS:
        raise ValueError(
            f"Kyber compression bits d must not exceed {MAX_COMPRESSION_BITS} "
            "(MAX_COMPRESSION_BITS)."
        )
    p = 2**bits
    if p < 2:
        raise ValueError("Kyber compression bits d must be at least 1.")
    counts: dict[int, int] = {}
    for value in range(q):
        level = round_fraction_nearest(value * p, q) % p
        reconstructed = round_fraction_nearest(level * q, p)
        noise = balanced_mod(reconstructed - value, q)
        counts[noise] = counts.get(noise, 0) + 1
    mean = sum(Fraction(value * count, q) for value, count in counts.items())
    mean_shift = round_fraction_value(mean)
    return pmf_from_fraction_map({value - mean_shift: Fraction(count, q) for value, count in counts.items()})


def round_fraction_nearest(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("Rounding denominator must be positive.")
    return (2 * numerator + denominator) // (2 * denominator)


def round_fraction_value(value: Fraction) -> int:
    if value >= 0:
        return round_fraction_nearest(value.numerator, value.denominator)
    return -round_fraction_nearest(-value.numerator, value.denominator)


def scaled_product_convolution(scale: Decimal, left: PMF, right: PMF, dimension: int) -> PMF:
    if scale == 0:
        return zero_pmf()
    return scale_pmf(convolve_power(multiply_pmfs(left, right), dimension), scale)


def ring_product_coefficient_pmfs(
    left: PMF,
    right: PMF,
    n: int,
    ring_type: str,
) -> tuple[PMF, ...]:
    profiles = coefficient_profiles(n, ring_type)
    product = multiply_pmfs(left, right)
    accumulated_ring_profile_work(0, product, profiles)
    return ring_product_pmfs_from_product(product, profiles)


def ring_product_pmfs_from_product(
    product: PMF,
    profiles: tuple[CoefficientProfile, ...],
) -> tuple[PMF, ...]:
    negative_product = scale_pmf(product, Decimal(-1))
    cache: dict[tuple[int, int], PMF] = {}
    results = []
    for profile in profiles:
        key = (profile.positive_terms, profile.negative_terms)
        if key not in cache:
            cache[key] = add_pmfs(
                convolve_power(product, profile.positive_terms),
                convolve_power(negative_product, profile.negative_terms),
            )
        results.append(cache[key])
    return tuple(results)


def scaled_ring_products(
    scale: Decimal,
    left: PMF,
    right: PMF,
    n: int,
    ring_type: str,
) -> tuple[PMF, ...]:
    profiles = coefficient_profiles(n, ring_type)
    if scale == 0:
        zero = zero_pmf()
        return (zero,) * len(profiles)
    product = multiply_pmfs(left, right)
    accumulated_ring_profile_work(0, product, profiles)
    return scaled_ring_products_from_product(scale, product, profiles)


def scaled_ring_products_from_product(
    scale: Decimal,
    product: PMF,
    profiles: tuple[CoefficientProfile, ...],
) -> tuple[PMF, ...]:
    cache: dict[int, PMF] = {}
    results = []
    for pmf in ring_product_pmfs_from_product(product, profiles):
        key = id(pmf)
        if key not in cache:
            cache[key] = scale_pmf(pmf, scale)
        results.append(cache[key])
    return tuple(results)


def accumulated_ring_profile_work(
    current: int,
    product: PMF,
    profiles: tuple[CoefficientProfile, ...],
) -> int:
    estimated = ring_profile_work(product, profiles)
    if estimated > MAX_RING_PROFILE_WORK - current:
        raise ValueError(
            "NTRU ring profile work exceeds MAX_RING_PROFILE_WORK "
            f"({MAX_RING_PROFILE_WORK}); reduce n, distribution support, or active product terms."
        )
    return current + estimated


def ring_profile_work(
    product: PMF,
    profiles: tuple[CoefficientProfile, ...],
) -> int:
    work = len(product.probabilities)
    profile_keys = {
        (profile.positive_terms, profile.negative_terms)
        for profile in profiles
    }
    for positive_terms, negative_terms in profile_keys:
        positive_support = convolution_power_support_bound(product, positive_terms)
        negative_support = convolution_power_support_bound(product, negative_terms)
        work = capped_work_add(
            work,
            repeated_power_work(positive_support, positive_terms),
            repeated_power_work(negative_support, negative_terms),
            capped_work_product(positive_support, negative_support),
            max(positive_support, negative_support),
        )
        if work > MAX_RING_PROFILE_WORK:
            return work
    return work


def convolution_power_support_bound(pmf: PMF, count: int) -> int:
    if count == 0 or len(pmf.probabilities) == 1:
        return 1
    values = pmf.probabilities.keys()
    if all(decimal_is_integer(value) for value in values):
        width = max(values) - min(values)
        if width > MAX_RING_PROFILE_WORK:
            return MAX_RING_PROFILE_WORK + 1
        return min(MAX_RING_PROFILE_WORK + 1, 1 + count * int(width))
    return capped_multiset_count(len(pmf.probabilities), count)


def capped_multiset_count(support_size: int, count: int) -> int:
    total = support_size + count - 1
    selected = min(support_size - 1, count)
    result = 1
    for index in range(1, selected + 1):
        result = result * (total - selected + index) // index
        if result > MAX_RING_PROFILE_WORK:
            return MAX_RING_PROFILE_WORK + 1
    return result


def repeated_power_work(support_bound: int, count: int) -> int:
    if count == 0:
        return 1
    return capped_work_product(support_bound, 2 * count.bit_length())


def capped_work_product(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    if left > MAX_RING_PROFILE_WORK // right:
        return MAX_RING_PROFILE_WORK + 1
    return left * right


def capped_work_add(*values: int) -> int:
    total = 0
    for value in values:
        if value > MAX_RING_PROFILE_WORK - total:
            return MAX_RING_PROFILE_WORK + 1
        total += value
    return total


def zero_pmf() -> PMF:
    return PMF({Decimal(0): Decimal(1)})


def add_pmfs(*pmfs: PMF) -> PMF:
    result = zero_pmf()
    for pmf in pmfs:
        result = convolve_pmfs(result, pmf)
    return result


def multiply_pmfs(left: PMF, right: PMF) -> PMF:
    ensure_pair_budget(left, right)
    probabilities: dict[Decimal, Decimal] = {}
    for left_value, left_probability in left.probabilities.items():
        for right_value, right_probability in right.probabilities.items():
            value = left_value * right_value
            probabilities[value] = probabilities.get(value, Decimal(0)) + left_probability * right_probability
            ensure_support_size(len(probabilities))
    return normalized_pmf(
        probabilities,
        tail_bound=combine_tail_bounds(left.tail_bound, right.tail_bound),
        warnings=combine_warnings(left, right),
    )


def convolve_pmfs(left: PMF, right: PMF) -> PMF:
    dense = dense_integer_convolution(left, right)
    if dense is not None:
        return normalized_pmf(
            dense,
            tail_bound=combine_tail_bounds(left.tail_bound, right.tail_bound),
            warnings=combine_warnings(left, right),
        )

    ensure_pair_budget(left, right)
    probabilities: dict[Decimal, Decimal] = {}
    for left_value, left_probability in left.probabilities.items():
        for right_value, right_probability in right.probabilities.items():
            value = left_value + right_value
            probabilities[value] = probabilities.get(value, Decimal(0)) + left_probability * right_probability
            ensure_support_size(len(probabilities))
    return normalized_pmf(
        probabilities,
        tail_bound=combine_tail_bounds(left.tail_bound, right.tail_bound),
        warnings=combine_warnings(left, right),
    )


def dense_integer_convolution(left: PMF, right: PMF) -> dict[Decimal, Decimal] | None:
    left_grid = integer_grid(left)
    right_grid = integer_grid(right)
    if left_grid is None or right_grid is None:
        return None
    left_minimum, left_values = left_grid
    right_minimum, right_values = right_grid
    output_size = len(left_values) + len(right_values) - 1
    ensure_support_size(output_size)
    values = karatsuba_convolution(left_values, right_values)
    start = left_minimum + right_minimum
    probabilities: dict[Decimal, Decimal] = {}
    for index, value in enumerate(values):
        probability = sanitized_probability(value)
        if probability != 0:
            probabilities[Decimal(start + index)] = probability
    return probabilities


def integer_grid(pmf: PMF) -> tuple[int, list[Decimal]] | None:
    if not pmf.probabilities or not all(
        safe_integer_grid_coordinate(value) for value in pmf.probabilities
    ):
        return None
    minimum = min(pmf.probabilities)
    maximum = max(pmf.probabilities)
    lower = int(minimum)
    upper = int(maximum)
    size = upper - lower + 1
    if size > MAX_PMF_SUPPORT:
        return None
    values = [Decimal(0)] * size
    for value, probability in pmf.probabilities.items():
        values[int(value) - lower] = probability
    return lower, values


def safe_integer_grid_coordinate(value: Any) -> bool:
    if not isinstance(value, Decimal) or not value.is_finite():
        return False
    if value != 0 and abs(value.adjusted()) > MAX_NUMERIC_EXPONENT_ABS:
        return False
    return abs(value) <= MAX_PMF_ABS_SUPPORT and decimal_is_integer(value)


def karatsuba_convolution(left: list[Decimal], right: list[Decimal]) -> list[Decimal]:
    if not left or not right:
        return []
    if min(len(left), len(right)) <= 64:
        result = [Decimal(0)] * (len(left) + len(right) - 1)
        for left_index, left_value in enumerate(left):
            if left_value == 0:
                continue
            for right_index, right_value in enumerate(right):
                if right_value != 0:
                    result[left_index + right_index] += left_value * right_value
        return result

    split = max(len(left), len(right)) // 2
    left_low, left_high = left[:split], left[split:]
    right_low, right_high = right[:split], right[split:]
    low = karatsuba_convolution(left_low, right_low)
    high = karatsuba_convolution(left_high, right_high)
    middle = karatsuba_convolution(add_polynomials(left_low, left_high), add_polynomials(right_low, right_high))
    subtract_polynomial_in_place(middle, low)
    subtract_polynomial_in_place(middle, high)

    result = [Decimal(0)] * (len(left) + len(right) - 1)
    add_polynomial_in_place(result, low, 0)
    add_polynomial_in_place(result, middle, split)
    add_polynomial_in_place(result, high, 2 * split)
    return result


def add_polynomials(left: list[Decimal], right: list[Decimal]) -> list[Decimal]:
    result = [Decimal(0)] * max(len(left), len(right))
    for index, value in enumerate(left):
        result[index] += value
    for index, value in enumerate(right):
        result[index] += value
    return result


def add_polynomial_in_place(target: list[Decimal], source: list[Decimal], offset: int) -> None:
    for index, value in enumerate(source):
        target[index + offset] += value


def subtract_polynomial_in_place(target: list[Decimal], source: list[Decimal]) -> None:
    for index, value in enumerate(source):
        target[index] -= value


def sanitized_probability(value: Decimal) -> Decimal:
    if value >= 0:
        return value
    rounding_error = Decimal(10) ** Decimal(-(getcontext().prec - 10))
    if abs(value) <= rounding_error:
        return Decimal(0)
    raise ValueError("High-precision convolution produced a negative probability.")


def convolve_power(pmf: PMF, count: int) -> PMF:
    if count < 0:
        raise ValueError("Convolution count must be non-negative.")
    result = zero_pmf()
    factor = pmf
    exponent = count
    while exponent:
        if exponent & 1:
            result = convolve_pmfs(result, factor)
        exponent //= 2
        if exponent:
            factor = convolve_pmfs(factor, factor)
    return result


def scale_pmf(pmf: PMF, factor: Decimal) -> PMF:
    if factor == 0:
        return zero_pmf()
    probabilities: dict[Decimal, Decimal] = {}
    for value, probability in pmf.probabilities.items():
        scaled = value * factor
        probabilities[scaled] = probabilities.get(scaled, Decimal(0)) + probability
    return normalized_pmf(probabilities, tail_bound=pmf.tail_bound, warnings=pmf.warnings)


def normalized_pmf(
    probabilities: dict[Decimal, Decimal],
    *,
    tail_bound: Decimal = Decimal(0),
    warnings: tuple[str, ...] = (),
) -> PMF:
    nonzero = {value: probability for value, probability in probabilities.items() if probability != 0}
    ensure_support_size(len(nonzero))
    total = sum(nonzero.values(), Decimal(0))
    if total <= 0:
        raise ValueError("A distribution must contain positive probability mass.")
    return PMF(
        probabilities={value: probability / total for value, probability in nonzero.items()},
        tail_bound=min(Decimal(1), max(Decimal(0), tail_bound)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def pmf_from_fraction_map(probabilities: dict[int, Fraction]) -> PMF:
    return normalized_pmf({Decimal(value): decimal_fraction(probability) for value, probability in probabilities.items()})


def aggregate_vector_failure(single_failure: Decimal, dimension: int) -> Decimal:
    return min(Decimal(1), single_failure * Decimal(dimension))


def coefficient_failure_probability(error: PMF, delta: Decimal) -> Decimal:
    return sum(
        probability
        for value, probability in error.probabilities.items()
        if abs(value) > delta
    )


def combine_tail_bounds(left: Decimal, right: Decimal) -> Decimal:
    return min(Decimal(1), left + right)


def combine_warnings(*pmfs: PMF) -> tuple[str, ...]:
    return tuple(dict.fromkeys(warning for pmf in pmfs for warning in pmf.warnings))


def pmf_summary(pmf: PMF) -> dict[str, Any]:
    return {
        "support_size": len(pmf.probabilities),
        "support": [decimal_text(min(pmf.probabilities)), decimal_text(max(pmf.probabilities))],
        "tail_probability_upper_bound": decimal_text(pmf.tail_bound),
    }


def ensure_pair_budget(left: PMF, right: PMF) -> None:
    if len(left.probabilities) * len(right.probabilities) > MAX_PAIR_PRODUCTS:
        raise ValueError(
            "Distribution operation exceeds the supported pair budget; reduce distribution support or dimension."
        )


def ensure_support_size(size: int) -> None:
    if size > MAX_PMF_SUPPORT:
        raise ValueError(
            "Distribution support exceeds the supported limit; reduce distribution support or dimension."
        )


def ensure_exact_domain_size(size: int, label: str) -> None:
    if size > MAX_PMF_SUPPORT:
        raise ValueError(
            f"{label} exact enumeration must not exceed {MAX_PMF_SUPPORT} "
            "(MAX_PMF_SUPPORT)."
        )


def value_of(spec: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in spec:
            return spec[name]
    if default is not None:
        return default
    joined = "/".join(names)
    raise ValueError(f"Missing distribution parameter: {joined}.")


def positive_int(raw: Any, label: str) -> int:
    value = nonnegative_int(raw, label)
    if value < 1:
        raise ValueError(f"{label} must be at least 1.")
    return value


def bounded_positive_int(
    raw: Any,
    label: str,
    maximum: int,
    maximum_name: str,
) -> int:
    value = safe_nonnegative_integer(raw, label, maximum, maximum_name)
    if value < 1:
        raise ValueError(f"{label} must be at least 1.")
    return value


def bounded_dimension(
    raw: Any,
    label: str,
    maximum: int,
    maximum_name: str,
) -> int:
    return bounded_positive_int(raw, label, maximum, maximum_name)


def nonnegative_int(raw: Any, label: str) -> int:
    return safe_nonnegative_integer(
        raw,
        label,
        MAX_SAFE_INTEGER_PARAMETER,
        "MAX_SAFE_INTEGER_PARAMETER",
    )


def safe_nonnegative_integer(
    raw: Any,
    label: str,
    maximum: int,
    maximum_name: str,
) -> int:
    invalid_message = f"{label} must be a non-negative integer."
    limit_message = f"{label} must not exceed {maximum} ({maximum_name})."
    value = bounded_decimal_number(
        raw,
        maximum,
        invalid_message,
        limit_message,
    )
    if value < 0 or not decimal_is_integer(value):
        raise ValueError(invalid_message)
    return int(value)


def bounded_finite_decimal(
    raw: Any,
    label: str,
    maximum: int,
    maximum_name: str,
) -> Decimal:
    limit_message = (
        f"{label} magnitude must not exceed {maximum} ({maximum_name})."
    )
    return bounded_decimal_number(
        raw,
        maximum,
        f"{label} must be a finite decimal number bounded by {maximum_name}.",
        limit_message,
    )


def bounded_decimal_number(
    raw: Any,
    maximum: int,
    invalid_message: str,
    limit_message: str,
) -> Decimal:
    if raw is None or isinstance(raw, bool):
        raise ValueError(invalid_message)
    if isinstance(raw, str):
        return bounded_decimal_text(
            raw,
            maximum,
            invalid_message,
            limit_message,
        )
    if isinstance(raw, int):
        if abs(raw) > maximum:
            raise ValueError(limit_message)
        return Decimal(raw)
    if isinstance(raw, float):
        if not math.isfinite(raw) or abs(raw) > maximum:
            raise ValueError(limit_message if math.isfinite(raw) else invalid_message)
        return bounded_decimal_text(
            str(raw),
            maximum,
            invalid_message,
            limit_message,
        )
    if isinstance(raw, Decimal):
        return validated_bounded_decimal(
            raw,
            maximum,
            invalid_message,
            limit_message,
        )
    raise ValueError(invalid_message)


def bounded_decimal_text(
    raw: str,
    maximum: int,
    invalid_message: str,
    limit_message: str,
) -> Decimal:
    if len(raw) > MAX_INTEGER_TEXT_LENGTH:
        raise ValueError(limit_message)
    text = raw.strip()
    match = DECIMAL_NUMBER_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(invalid_message)
    exponent = match.group("exponent")
    if exponent is not None and exponent_magnitude_exceeds_limit(exponent):
        raise ValueError(invalid_message)
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(invalid_message) from exc
    return validated_bounded_decimal(
        value,
        maximum,
        invalid_message,
        limit_message,
    )


def exponent_magnitude_exceeds_limit(exponent: str) -> bool:
    digits = exponent.lstrip("+-").lstrip("0") or "0"
    maximum = str(MAX_NUMERIC_EXPONENT_ABS)
    return len(digits) > len(maximum) or (
        len(digits) == len(maximum) and digits > maximum
    )


def validated_bounded_decimal(
    value: Decimal,
    maximum: int,
    invalid_message: str,
    limit_message: str,
) -> Decimal:
    if not value.is_finite():
        raise ValueError(invalid_message)
    if abs(value) > maximum:
        raise ValueError(limit_message)
    if value == 0:
        _, _, exponent = value.as_tuple()
        if abs(exponent) > MAX_NUMERIC_EXPONENT_ABS:
            raise ValueError(limit_message)
    elif abs(value.adjusted()) > MAX_NUMERIC_EXPONENT_ABS:
        raise ValueError(limit_message)
    return value


def decimal_is_integer(value: Decimal) -> bool:
    return value == value.to_integral_value()


def ceiling_int(raw: Any, label: str) -> int:
    return bounded_rounding_int(raw, label, ROUND_CEILING)


def floor_int(raw: Any, label: str) -> int:
    return bounded_rounding_int(raw, label, ROUND_FLOOR)


def bounded_rounding_int(raw: Any, label: str, rounding: str) -> int:
    invalid_message = f"{label} must be a finite number."
    limit_message = (
        f"{label} magnitude must not exceed {MAX_SAFE_INTEGER_PARAMETER} "
        "(MAX_SAFE_INTEGER_PARAMETER)."
    )
    value = bounded_decimal_number(
        raw,
        MAX_SAFE_INTEGER_PARAMETER + 1,
        invalid_message,
        limit_message,
    )
    rounded = value.to_integral_value(rounding=rounding)
    if abs(rounded) > MAX_SAFE_INTEGER_PARAMETER:
        raise ValueError(limit_message)
    return int(rounded)


def positive_scalar(raw: Any, label: str) -> Decimal:
    value = scalar(raw, label)
    if value <= 0:
        raise ValueError(f"{label} must be greater than 0.")
    return value


def nonnegative_scalar(raw: Any, label: str) -> Decimal:
    value = scalar(raw, label)
    if value < 0:
        raise ValueError(f"{label} must be non-negative.")
    return value


def scalar(raw: Any, label: str) -> Decimal:
    if raw is None or isinstance(raw, bool):
        raise ValueError(f"{label} must be a finite number.")
    text = str(raw).strip()
    if text == "sqrt(2)":
        return Decimal(2).sqrt()
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number or sqrt(2).") from exc
    if not value.is_finite():
        raise ValueError(f"{label} must be a finite number.")
    return value


def decimal_digits_for_bits(bits: int) -> int:
    return math.ceil(bits * math.log10(2)) + 12


def decimal_fraction(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)


def decimal_text(value: Decimal, significant_digits: int = 40) -> str:
    if value == 0:
        return "0"
    return format(value, f".{significant_digits}E")


def log2_text(value: Decimal) -> str:
    if value == 0:
        return "-Infinity"
    return format(value.ln() / Decimal(2).ln(), ".24f")
