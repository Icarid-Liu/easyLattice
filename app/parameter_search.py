from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import load_config


RING_DIMENSIONS = (512, 1024, 2048, 4096, 8192)
TERNARY_RING_DIMENSIONS = (384, 512, 768, 1024, 1152, 1536, 2048, 2304, 3072, 4096, 4608, 6144, 8192)
ETA_VALUES = (1, 2, 3, 4, 5, 6, 8)
SPARSE_TERNARY_PARAMETERS = (
    (1, 0),
    (2, 0),
    (2, 1),
    (3, 0),
    (3, 1),
    (3, 2),
    (4, 0),
    (4, 1),
    (4, 2),
)
COMMON_NTT_PRIMES = (
    257,
    769,
    3329,
    7681,
    12289,
    40961,
    65537,
    786433,
    1179649,
    7340033,
    8380417,
    167772161,
    469762049,
    998244353,
    2013265921,
)
SUPPORTED_SECURITY_MODELS = {"classical", "quantum", "min", "matzov"}
SUPPORTED_RED_COST_MODELS = {"matzov", "adps16"}
SUPPORTED_RING_FAMILIES = {"power2", "ternary"}


@dataclass(frozen=True)
class RequestOptions:
    target_security: int = 128
    ring_family: str = "power2"
    security_model: str = "min"
    red_cost_model: str = "matzov"
    ntt_scale_power: int = 0
    min_q_bits: int = 2
    max_q_bits: int = 24
    min_n: int = 512
    max_n: int = 8192
    distribution: str = "auto"
    use_estimator: bool = False
    estimator_timeout: int = 16
    validation_count: int = 1
    validation_attempts: int = 1


@dataclass(frozen=True)
class DistributionSpec:
    family: str
    name: str
    parameters: dict[str, Any]
    mean: float
    variance: float
    stddev: float
    support: list[int]
    symmetric: bool
    sampling: str
    estimator: dict[str, Any]


def recommend_rlwe(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return an RLWE recommendation and a small list of alternatives."""
    request = parse_request(raw or {})
    started = time.perf_counter()

    raw_candidates = build_candidates(request)
    candidates = select_best_distribution_per_modulus(raw_candidates, request)
    if not candidates:
        raise ValueError("No candidates could be generated for the requested bounds.")

    viable = [c for c in candidates if meets_target(c["security"], request)]
    ranked = sorted(viable or candidates, key=lambda c: candidate_rank(c, request))

    estimator_result = None
    if request.use_estimator:
        estimator_result = {"ok": True, "validated": []}
        validated_candidates: list[dict[str, Any]] = []
        accepted_moduli: set[tuple[str, int, int]] = set()
        max_validation_attempts = min(len(raw_candidates), max(request.validation_count, request.validation_attempts))
        validation_pool = sorted(raw_candidates, key=lambda c: estimator_candidate_rank(c, request))
        attempts = 0
        for candidate in validation_pool:
            key = (
                str(candidate["ring"]["family_id"]),
                int(candidate["ring"]["n"]),
                int(candidate["modulus"]["q"]),
            )
            if key in accepted_moduli:
                continue
            if attempts >= max_validation_attempts:
                break
            attempts += 1
            result = run_sage_estimator(candidate, request.estimator_timeout)
            estimator_result["validated"].append(result)
            if result.get("ok"):
                apply_estimator_result(candidate, result, request)
                validated_candidates.append(candidate)
                if meets_target(candidate["security"], request):
                    accepted_moduli.add(key)
            else:
                estimator_result["ok"] = False
                candidate["warnings"].append(result["message"])
        validated_viable = select_best_distribution_per_modulus(
            [c for c in validated_candidates if meets_target(c["security"], request)],
            request,
        )
        if validated_viable:
            ranked = sorted(validated_viable, key=lambda c: candidate_rank(c, request))
        else:
            estimator_result["ok"] = False
            viable = [c for c in ranked if meets_target(c["security"], request)]
            ranked = sorted(viable or ranked, key=lambda c: candidate_rank(c, request))

    recommendation = ranked[0]
    alternatives = ranked[1:5]

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return {
        "request": asdict(request),
        "recommendation": recommendation,
        "alternatives": alternatives,
        "estimator": estimator_result,
        "search": {
            "elapsed_ms": elapsed_ms,
            "generated_candidates": len(raw_candidates),
            "modulus_candidates": len(candidates),
            "viable_candidates": len(viable),
            "strategy": [
                f"ring family first: {request.ring_family}",
                "degree n second",
                f"prime q with {ntt_requirement_label(request.ntt_scale_power, request.ring_family)} | q-1",
                "choose Xs/Xe distribution pair after q; prototype currently uses the same family for both",
                f"fast {request.red_cost_model.upper()} screen before optional Sage validation",
            ],
        },
        "next_question": (
            "是否接受该 RLWE 实例？如果要绑定到具体加密或签名方案，下一步需要加入"
            "正确性/拒绝率/光滑参数等方案相关约束。"
        ),
    }


def parse_request(raw: dict[str, Any]) -> RequestOptions:
    target = int(raw.get("target_security", raw.get("targetSecurity", 128)))
    if target < 40 or target > 512:
        raise ValueError("target_security must be between 40 and 512 bits.")

    model = str(raw.get("security_model", raw.get("securityModel", "min"))).lower()
    if model not in SUPPORTED_SECURITY_MODELS:
        raise ValueError("security_model must be one of classical, quantum, min, matzov.")

    ring_family = str(raw.get("ring_family", raw.get("ringFamily", "power2"))).lower()
    if ring_family not in SUPPORTED_RING_FAMILIES:
        raise ValueError("ring_family must be one of power2, ternary.")

    red_cost_model = str(raw.get("red_cost_model", raw.get("redCostModel", "matzov"))).lower()
    if red_cost_model not in SUPPORTED_RED_COST_MODELS:
        raise ValueError("red_cost_model must be one of matzov, adps16.")

    ntt_scale_power = int(raw.get("ntt_scale_power", raw.get("nttScalePower", 0)))
    if ntt_scale_power < -1 or ntt_scale_power > 8:
        raise ValueError("ntt_scale_power must be between -1 and 8.")

    min_q_bits = int(raw.get("min_q_bits", raw.get("minQBits", 2)))
    if min_q_bits < 2 or min_q_bits > 63:
        raise ValueError("min_q_bits must be between 2 and 63.")

    max_q_bits = int(raw.get("max_q_bits", raw.get("maxQBits", 24)))
    if max_q_bits < min_q_bits or max_q_bits > 63:
        raise ValueError("max_q_bits must be between min_q_bits and 63.")

    min_n = int(raw.get("min_n", raw.get("minN", 512)))
    max_n = int(raw.get("max_n", raw.get("maxN", 8192)))
    if min_n < 256 or max_n < min_n:
        raise ValueError("Require 256 <= min_n <= max_n.")

    distribution = str(raw.get("distribution", "auto"))
    if distribution not in {"auto", "centered_binomial", "sparse_ternary"}:
        raise ValueError("distribution must be one of auto, centered_binomial, sparse_ternary.")

    estimator_timeout = raw.get(
        "estimator_timeout",
        raw.get("estimatorTimeout", load_config().estimator.default_timeout_seconds),
    )
    validation_count = max(1, min(12, int(raw.get("validation_count", raw.get("validationCount", 1)))))
    validation_attempts = raw.get(
        "validation_attempts",
        raw.get("validationAttempts", validation_count if validation_count == 1 else validation_count * 4),
    )

    return RequestOptions(
        target_security=target,
        ring_family=ring_family,
        security_model=model,
        red_cost_model=red_cost_model,
        ntt_scale_power=ntt_scale_power,
        min_q_bits=min_q_bits,
        max_q_bits=max_q_bits,
        min_n=min_n,
        max_n=max_n,
        distribution=distribution,
        use_estimator=bool(raw.get("use_estimator", raw.get("useEstimator", False))),
        estimator_timeout=max(4, min(90, int(estimator_timeout))),
        validation_count=validation_count,
        validation_attempts=max(validation_count, min(80, int(validation_attempts))),
    )


def build_candidates(request: RequestOptions) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for n in ring_dimensions(request.ring_family):
        if n < request.min_n or n > request.max_n:
            continue
        primes = ntt_prime_candidates(
            n=n,
            max_q_bits=request.max_q_bits,
            ntt_scale_power=request.ntt_scale_power,
            min_q_bits=request.min_q_bits,
            ring_family=request.ring_family,
        )
        for q in primes:
            for distribution in distribution_candidates(n, request):
                candidate = make_candidate(n=n, q=q, distribution=distribution, request=request)
                candidates.append(candidate)
    return candidates


def select_best_distribution_per_modulus(
    candidates: list[dict[str, Any]],
    request: RequestOptions,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (
            str(candidate["ring"]["family_id"]),
            int(candidate["ring"]["n"]),
            int(candidate["modulus"]["q"]),
        )
        grouped.setdefault(key, []).append(candidate)

    selected = []
    for group in grouped.values():
        selected.append(min(group, key=lambda c: distribution_rank(c, request)))
    return selected


def make_candidate(n: int, q: int, distribution: DistributionSpec, request: RequestOptions) -> dict[str, Any]:
    factors = factor_integer(q - 1)
    security = fast_security_estimate(
        n=n,
        q=q,
        sigma=distribution.stddev,
        sparse_penalty_bits=float(distribution.estimator.get("fast_screen_penalty_bits", 0.0)),
    )
    ring = ring_profile(n=n, q=q, family=request.ring_family)
    ntt = ntt_profile(n=n, q=q, factors=factors, ring_family=request.ring_family)
    warnings = [
        "当前为 RLWE/LWE 快速筛选；未绑定具体方案，因此不计算解密错误率或签名失败率。",
    ]

    return {
        "ring": {
            **ring,
        },
        "modulus": {
            "q": q,
            "bits": q.bit_length(),
            "prime": True,
            "q_minus_1_factorization": format_factorization(factors),
            "ntt_condition": ntt["condition"],
            "ntt_friendly": True,
            "ntt_quality": ntt["quality"],
            "ntt_layers_remaining": ntt["layers_remaining"],
            "polynomial_factorization": ntt["polynomial_factorization"],
            "factor_count": ntt["factor_count"],
            "factor_degree": ntt["factor_degree"],
            "decomposition_score": ntt["score"],
            "two_adicity": ntt["two_adicity"],
            "small_factor_weight": ntt["small_factor_weight"],
        },
        "distribution": {
            "family": distribution.family,
            "name": distribution.name,
            "parameters": distribution.parameters,
            "secret": distribution_profile(distribution),
            "error": distribution_profile(distribution),
            "estimator": distribution.estimator,
        },
        "security": security,
        "selection": {
            "target_security": request.target_security,
            "security_model": request.security_model,
            "selected_security_bits": selected_security_bits(security, request),
            "margin_bits": security_margin_bits(security, request),
            "meets_target": meets_target(security, request),
            "rank_score": None,
        },
        "warnings": warnings,
    }


def distribution_candidates(n: int, request: RequestOptions) -> list[DistributionSpec]:
    candidates: list[DistributionSpec] = []
    if request.distribution in {"auto", "centered_binomial"}:
        candidates.extend(centered_binomial_spec(eta) for eta in ETA_VALUES)
    if request.distribution in {"auto", "sparse_ternary"}:
        for l0, l1 in SPARSE_TERNARY_PARAMETERS:
            spec = sparse_ternary_spec(n=n, l0=l0, l1=l1)
            if spec.estimator["plus_weight"] >= 1 and spec.estimator["minus_weight"] >= 1:
                candidates.append(spec)
    return candidates


def centered_binomial_spec(eta: int) -> DistributionSpec:
    variance = eta / 2.0
    return DistributionSpec(
        family="centered_binomial",
        name=f"CBD({eta})",
        parameters={"eta": eta},
        mean=0.0,
        variance=variance,
        stddev=math.sqrt(variance),
        support=[-eta, eta],
        symmetric=True,
        sampling="bit-sliced popcount friendly",
        estimator={"type": "centered_binomial", "eta": eta},
    )


def sparse_ternary_spec(n: int, l0: int, l1: int) -> DistributionSpec:
    probability_each = ((2**l0) - 1) / (2 ** (2 * l0 + l1))
    variance = 2 * probability_each
    plus_weight = max(0, round(n * probability_each))
    minus_weight = max(0, round(n * probability_each))
    estimator_stddev = math.sqrt((plus_weight + minus_weight) / n) if n else 0.0
    return DistributionSpec(
        family="sparse_ternary",
        name=f"ST(l0={l0}, l1={l1})",
        parameters={
            "l0": l0,
            "l1": l1,
            "probability_plus": probability_each,
            "probability_minus": probability_each,
            "probability_zero": 1 - 2 * probability_each,
        },
        mean=0.0,
        variance=variance,
        stddev=math.sqrt(variance),
        support=[-1, 1],
        symmetric=True,
        sampling="sample sign/magnitude from bit arithmetic; zero otherwise",
        estimator={
            "type": "sparse_ternary_fixed_weight",
            "plus_weight": plus_weight,
            "minus_weight": minus_weight,
            "iid_stddev": math.sqrt(variance),
            "fixed_weight_stddev": estimator_stddev,
            "note": "fixed-weight approximation to the iid sparse ternary distribution",
            "fast_screen_penalty_bits": 30.0,
        },
    )


def distribution_profile(distribution: DistributionSpec) -> dict[str, Any]:
    return {
        "name": distribution.name,
        "mean": distribution.mean,
        "variance": round(distribution.variance, 9),
        "stddev": round(distribution.stddev, 9),
        "support": distribution.support,
        "symmetric": distribution.symmetric,
        "sampling": distribution.sampling,
    }


def fast_security_estimate(n: int, q: int, sigma: float, sparse_penalty_bits: float = 0.0) -> dict[str, Any]:
    beta = estimate_bkz_beta(n=n, q=q, sigma=sigma)
    classical = floor_bits(max(0.0, 0.292 * beta - sparse_penalty_bits))
    quantum = floor_bits(max(0.0, 0.265 * beta - sparse_penalty_bits))
    return {
        "source": "fast-screen",
        "classical_bits": classical,
        "quantum_bits": quantum,
        "matzov_bits": classical,
        "adps16_core_svp_bits": classical,
        "attacks": {
            "matzov_proxy_screen": {
                "bkz_beta": beta,
                "classical_bits": classical,
                "quantum_bits": quantum,
                "matzov_bits": classical,
                "adps16_core_svp_bits": classical,
                "sparse_penalty_bits": sparse_penalty_bits,
                "cost_model": "ADPS16/MATZOV-style first-pass screen",
            }
        },
        "notes": [
            "This is a screening estimate calibrated to lattice-estimator Matzov/dual-hybrid rough outputs, not a proof.",
            "Sparse ternary candidates include a conservative fast-screen penalty for sparse-secret attacks.",
            "Use Sage/lattice-estimator validation before relying on a parameter set.",
        ],
    }


def estimate_bkz_beta(n: int, q: int, sigma: float) -> int:
    # The exponent constant is a pragmatic calibration for the first-pass screen.
    # The Sage estimator path is the authority when available.
    exponent_constant = 3.7 if n < 1024 else 3.5
    delta_required = (q / sigma) ** (1.0 / (exponent_constant * n))
    for beta in range(40, 2600):
        if root_hermite_factor(beta) <= delta_required:
            return beta
    return 2600


def root_hermite_factor(beta: int) -> float:
    if beta <= 2:
        return 1.0219
    numerator = (math.pi * beta) ** (1.0 / beta) * beta
    denominator = 2.0 * math.pi * math.e
    return (numerator / denominator) ** (1.0 / (2.0 * (beta - 1)))


def meets_target(security: dict[str, Any], request: RequestOptions) -> bool:
    return security_margin_bits(security, request) >= 0


def selected_security_bits(security: dict[str, Any], request: RequestOptions) -> float:
    classical = float(security["classical_bits"])
    quantum = float(security["quantum_bits"])
    if request.red_cost_model == "adps16":
        adps16 = security.get("adps16_core_svp_bits", classical)
        return float(adps16) if adps16 is not None else float("-inf")
    if request.red_cost_model == "matzov":
        matzov = security.get("matzov_bits")
        return float(matzov) if matzov is not None else float("-inf")
    if request.security_model == "classical":
        return classical
    if request.security_model == "quantum":
        return quantum
    if request.security_model == "matzov":
        matzov = security.get("matzov_bits")
        return float(matzov) if matzov is not None else float("-inf")
    return min(classical, quantum)


def security_margin_bits(security: dict[str, Any], request: RequestOptions) -> float:
    selected = selected_security_bits(security, request)
    if not math.isfinite(selected):
        return float("-inf")
    return round(selected - request.target_security, 3)


def candidate_rank(candidate: dict[str, Any], request: RequestOptions) -> tuple[float, ...]:
    margin = security_margin_bits(candidate["security"], request)
    ring_rank = 0 if candidate["ring"]["family_id"] == request.ring_family else 1
    n = int(candidate["ring"]["n"])
    q = int(candidate["modulus"]["q"])
    q_bits = int(candidate["modulus"]["bits"])
    stddev = float(candidate["distribution"]["secret"]["stddev"])
    ntt_score = float(candidate["modulus"]["decomposition_score"])
    ntt_layers_remaining = int(candidate["modulus"]["ntt_layers_remaining"])
    overkill = max(0.0, margin)
    shortage = abs(min(0.0, margin)) * 10_000.0
    rank = (shortage, ring_rank, n, q, q_bits, ntt_layers_remaining, overkill, stddev, -ntt_score)
    candidate["selection"]["selected_security_bits"] = selected_security_bits(candidate["security"], request)
    candidate["selection"]["margin_bits"] = margin
    candidate["selection"]["rank_score"] = rank
    return rank


def distribution_rank(candidate: dict[str, Any], request: RequestOptions) -> tuple[float, ...]:
    margin = security_margin_bits(candidate["security"], request)
    stddev = float(candidate["distribution"]["secret"]["stddev"])
    family_rank = 0 if candidate["distribution"]["family"] == "sparse_ternary" else 1
    overkill = max(0.0, margin)
    shortage = abs(min(0.0, margin)) * 10_000.0
    return (shortage, overkill, stddev, family_rank)


def estimator_candidate_rank(candidate: dict[str, Any], request: RequestOptions) -> tuple[float, ...]:
    ring_rank = 0 if candidate["ring"]["family_id"] == request.ring_family else 1
    n = int(candidate["ring"]["n"])
    q = int(candidate["modulus"]["q"])
    return (ring_rank, n, q, *distribution_rank(candidate, request))


def ring_dimensions(family: str) -> tuple[int, ...]:
    if family == "ternary":
        return tuple(n for n in TERNARY_RING_DIMENSIONS if only_has_prime_factors(n, {2, 3}) and n % 2 == 0)
    return RING_DIMENSIONS


def ring_profile(n: int, q: int, family: str) -> dict[str, Any]:
    if family == "ternary":
        polynomial = f"x^{n} - x^{n // 2} + 1"
        return {
            "family_id": "ternary",
            "family": "ternary cyclotomic",
            "n": n,
            "cyclotomic_index": 3 * n,
            "polynomial": polynomial,
            "quotient": f"Z_{q}[x] / ({polynomial})",
            "dimension_rule": "n has only 2 and 3 as prime factors",
        }
    polynomial = f"x^{n} + 1"
    return {
        "family_id": "power2",
        "family": "2-power cyclotomic",
        "n": n,
        "cyclotomic_index": 2 * n,
        "polynomial": polynomial,
        "quotient": f"Z_{q}[x] / ({polynomial})",
        "dimension_rule": "n is a power of 2",
    }


def only_has_prime_factors(value: int, allowed: set[int]) -> bool:
    remaining = value
    for prime in allowed:
        while remaining % prime == 0:
            remaining //= prime
    return remaining == 1


def ntt_prime_candidates(
    n: int,
    max_q_bits: int,
    ntt_scale_power: int = 0,
    min_q_bits: int = 2,
    ring_family: str = "power2",
    limit: int = 96,
) -> list[int]:
    modulus = ntt_divisor(n, ntt_scale_power, ring_family)
    found = {
        q
        for q in COMMON_NTT_PRIMES
        if min_q_bits <= q.bit_length() <= max_q_bits
        and q > modulus
        and (q - 1) % modulus == 0
        and is_prime(q)
    }

    min_bits = max(min_q_bits, (modulus + 1).bit_length())
    bit_targets = range(min_bits, max_q_bits + 1)
    for bits in bit_targets:
        start_k = max(1, ((1 << (bits - 1)) - 1) // modulus)
        stop_k = max(start_k + 1, ((1 << bits) - 1) // modulus)
        step = max(1, (stop_k - start_k) // 5000)
        added_for_bits = 0
        for k in range(start_k, stop_k + 1, step):
            q = k * modulus + 1
            if q.bit_length() > max_q_bits:
                continue
            if is_prime(q):
                found.add(q)
                added_for_bits += 1
                if added_for_bits >= 10:
                    break

    scored = sorted(found, key=lambda q: (q, -ntt_profile(n, q, factor_integer(q - 1), ring_family)["score"]))
    return scored[:limit]


def ntt_divisor(n: int, ntt_scale_power: int, ring_family: str = "power2") -> int:
    base = 3 * n if ring_family == "ternary" else n
    if ntt_scale_power < 0:
        return 2 * base
    return max(1, base // (2**ntt_scale_power))


def ntt_requirement_label(ntt_scale_power: int, ring_family: str = "power2") -> str:
    if ring_family == "ternary":
        if ntt_scale_power < 0:
            return "6n"
        if ntt_scale_power == 0:
            return "3n"
        if ntt_scale_power == 1:
            return "3n/2"
        return f"3n/2^{ntt_scale_power}"
    if ntt_scale_power < 0:
        return "2n"
    if ntt_scale_power == 0:
        return "n"
    if ntt_scale_power == 1:
        return "n/2"
    return f"n/2^{ntt_scale_power}"


def ntt_profile(n: int, q: int, factors: dict[int, int] | None = None, ring_family: str = "power2") -> dict[str, Any]:
    factors = factors or factor_integer(q - 1)
    two_adicity = factors.get(2, 0)
    three_adicity = factors.get(3, 0)
    if ring_family == "ternary":
        return ternary_ntt_profile(n, q, factors, two_adicity, three_adicity)

    return power2_ntt_profile(n, q, factors, two_adicity)


def power2_ntt_profile(n: int, q: int, factors: dict[int, int], two_adicity: int) -> dict[str, Any]:
    n_power = int(math.log2(n))
    small_factor_weight = sum(exp for prime, exp in factors.items() if prime <= 31)
    odd_factor_variety = sum(1 for prime in factors if prime != 2)
    if two_adicity >= n_power + 1:
        quality = "full_split"
        condition = f"{2 * n} | q - 1"
        layers_remaining = 0
        factor_count = n
        factor_degree = 1
        split_text = f"x^{n} + 1 splits into {n} linear factors over F_q"
        split_bonus = 14
    elif two_adicity >= 1:
        factor_degree = 2 ** max(1, n_power + 1 - two_adicity)
        factor_count = max(1, n // factor_degree)
        layers_remaining = int(math.log2(factor_degree))
        quality = "one_layer_remaining" if layers_remaining == 1 else f"{layers_remaining}_layers_remaining"
        divisor = 2**two_adicity
        condition = f"{divisor} | q - 1; {2 * divisor} ∤ q - 1"
        degree_name = {
            2: "quadratic",
            4: "quartic",
            8: "degree-8",
            16: "degree-16",
        }.get(factor_degree, f"degree-{factor_degree}")
        split_text = f"x^{n} + 1 splits into {factor_count} {degree_name} factors over F_q"
        split_bonus = max(0, 12 - layers_remaining)
    else:
        quality = "not_ntt_target"
        condition = f"{n} ∤ q - 1"
        layers_remaining = 99
        factor_count = 0
        factor_degree = 0
        split_text = "not selected for the NTT target"
        split_bonus = -100
    score = split_bonus + two_adicity * 3 + small_factor_weight * 2 + odd_factor_variety
    return {
        "condition": condition,
        "quality": quality,
        "layers_remaining": layers_remaining,
        "factor_count": factor_count,
        "factor_degree": factor_degree,
        "polynomial_factorization": split_text,
        "two_adicity": two_adicity,
        "small_factor_weight": small_factor_weight,
        "score": score,
    }


def ternary_ntt_profile(
    n: int,
    q: int,
    factors: dict[int, int],
    two_adicity: int,
    three_adicity: int,
) -> dict[str, Any]:
    n_power = 0
    temp = n
    while temp % 2 == 0:
        n_power += 1
        temp //= 2
    required_three = 1
    temp_three = n
    while temp_three % 3 == 0:
        required_three += 1
        temp_three //= 3
    has_3 = three_adicity >= required_three
    small_factor_weight = sum(exp for prime, exp in factors.items() if prime <= 31)
    odd_factor_variety = sum(1 for prime in factors if prime != 2)
    if not has_3:
        quality = "not_ntt_target"
        condition = f"3 ∤ q - 1"
        layers_remaining = 99
        factor_count = 0
        factor_degree = 0
        split_text = "not selected for the ternary cyclotomic NTT target"
        split_bonus = -100
    else:
        available_two = min(two_adicity, n_power)
        factor_degree = 2 ** max(0, n_power - available_two)
        factor_count = max(1, n // max(1, factor_degree))
        layers_remaining = int(math.log2(max(1, factor_degree)))
        quality = "full_split" if layers_remaining == 0 else (
            "one_layer_remaining" if layers_remaining == 1 else f"{layers_remaining}_layers_remaining"
        )
        divisor = (3**required_three) * (2**available_two)
        condition = f"{divisor} | q - 1"
        degree_name = {
            1: "linear",
            2: "quadratic",
            4: "quartic",
            8: "degree-8",
            16: "degree-16",
        }.get(factor_degree, f"degree-{factor_degree}")
        split_text = f"x^{n} - x^{n // 2} + 1 splits into {factor_count} {degree_name} factors over F_q"
        split_bonus = max(0, 14 - layers_remaining)
    score = split_bonus + two_adicity * 3 + three_adicity * 3 + small_factor_weight * 2 + odd_factor_variety
    return {
        "condition": condition,
        "quality": quality,
        "layers_remaining": layers_remaining,
        "factor_count": factor_count,
        "factor_degree": factor_degree,
        "polynomial_factorization": split_text,
        "two_adicity": two_adicity,
        "three_adicity": three_adicity,
        "small_factor_weight": small_factor_weight,
        "score": score,
    }


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    small_primes = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for p in small_primes:
        if n == p:
            return True
        if n % p == 0:
            return False

    d = n - 1
    s = 0
    while d % 2 == 0:
        s += 1
        d //= 2

    for a in (2, 3, 5, 7, 11, 13, 17):
        if a >= n:
            continue
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def factor_integer(n: int) -> dict[int, int]:
    factors: dict[int, int] = {}
    divisor = 2
    while divisor * divisor <= n:
        while n % divisor == 0:
            factors[divisor] = factors.get(divisor, 0) + 1
            n //= divisor
        divisor = 3 if divisor == 2 else divisor + 2
        if divisor > 10_000 and n > 1 and is_prime(n):
            break
    if n > 1:
        factors[n] = factors.get(n, 0) + 1
    return factors


def format_factorization(factors: dict[int, int]) -> str:
    pieces = []
    for prime in sorted(factors):
        exp = factors[prime]
        pieces.append(str(prime) if exp == 1 else f"{prime}^{exp}")
    return " * ".join(pieces)


def run_sage_estimator(candidate: dict[str, Any], timeout: int) -> dict[str, Any]:
    config = load_config()
    sage_binary = config.estimator.sage_binary
    sage = shutil.which(sage_binary) or (sage_binary if Path(sage_binary).exists() else None)
    if not sage:
        return {
            "ok": False,
            "message": f"Sage binary '{sage_binary}' not found; using fast-screen estimate only.",
        }

    runner = Path(__file__).with_name("estimator_runner.py")
    payload = {
        "n": candidate["ring"]["n"],
        "q": candidate["modulus"]["q"],
        "distribution": candidate["distribution"],
        "per_attack_timeout": max(3, min(30, config.estimator.per_attack_timeout_seconds or timeout // 2)),
    }
    env = os.environ.copy()
    if config.estimator.lattice_estimator_path:
        existing = env.get("PYTHONPATH")
        estimator_path = str(Path(config.estimator.lattice_estimator_path).expanduser())
        env["PYTHONPATH"] = estimator_path if not existing else f"{estimator_path}{os.pathsep}{existing}"

    try:
        completed = subprocess.run(
            [sage, "-python", str(runner)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "message": f"Sage/lattice-estimator timed out after {timeout}s; keeping fast-screen estimate.",
        }

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        suffix = f" Detail: {detail[0]}" if detail else ""
        return {
            "ok": False,
            "message": f"Sage/lattice-estimator failed with exit code {completed.returncode}.{suffix}",
        }

    try:
        data = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {
            "ok": False,
            "message": "Sage/lattice-estimator returned non-JSON output; keeping fast-screen estimate.",
        }

    if not data.get("ok"):
        return {
            "ok": False,
            "message": data.get("message", "Sage/lattice-estimator could not estimate this candidate."),
            "raw": data,
        }
    return data


def apply_estimator_result(
    candidate: dict[str, Any],
    estimator_result: dict[str, Any],
    request: RequestOptions,
) -> None:
    classical = estimator_result["modes"].get("classical", {})
    quantum = estimator_result["modes"].get("quantum", {})
    classical_bits = classical.get("min_bits", candidate["security"]["classical_bits"])
    quantum_bits = quantum.get("min_bits", candidate["security"]["quantum_bits"])
    candidate["security"] = {
        "source": "sage-lattice-estimator",
        "classical_bits": floor_bits(float(classical_bits)),
        "quantum_bits": floor_bits(float(quantum_bits)),
        "matzov_bits": floor_optional_bits(matzov_bits(estimator_result)),
        "matzov_quantum_bits": floor_optional_bits(matzov_bits(estimator_result, mode="quantum")),
        "adps16_core_svp_bits": floor_optional_bits(adps16_core_svp_bits(estimator_result)),
        "attacks": estimator_result["modes"],
        "estimator_commit": estimator_result.get("estimator_commit"),
        "notes": [
            "Estimated as an LWE instance with n RLWE samples; use full scheme analysis for production.",
            "Classical and quantum modes use the estimator ADPS16 cost model variants.",
        ],
    }
    candidate["selection"]["selected_security_bits"] = selected_security_bits(candidate["security"], request)
    candidate["selection"]["margin_bits"] = security_margin_bits(candidate["security"], request)
    candidate["selection"]["meets_target"] = meets_target(candidate["security"], request)
    candidate["warnings"].append("Sage/lattice-estimator rough validation was applied to this recommendation.")


def matzov_bits(estimator_result: dict[str, Any], mode: str = "classical") -> float | None:
    mode_result = estimator_result.get("modes", {}).get(mode, {})
    attack = mode_result.get("attacks", {}).get("dual_hybrid", {})
    if attack.get("ok") and attack.get("rop_bits") is not None:
        return float(attack["rop_bits"])
    return None


def adps16_core_svp_bits(estimator_result: dict[str, Any], mode: str = "classical") -> float | None:
    mode_result = estimator_result.get("modes", {}).get(mode, {})
    attack = mode_result.get("attacks", {}).get("usvp", {})
    if attack.get("ok") and attack.get("rop_bits") is not None:
        return float(attack["rop_bits"])
    return None


def floor_bits(value: float, digits: int = 1) -> float:
    scale = 10**digits
    return math.floor(float(value) * scale) / scale


def floor_optional_bits(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return floor_bits(value, digits)


def cli() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(recommend_rlwe(payload), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
