from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from itertools import product
from typing import Any

from .compression_noise import compression_noise_profile
from .config import AppConfig, load_config
from .estimator_process import estimator_profile_for, run_estimator
from .security_result import modulus_bits, selection_status, validation_result


RING_DIMENSIONS = (512, 1024, 2048, 4096, 8192)
TERNARY_RING_DIMENSIONS = (384, 512, 768, 1024, 1152, 1536, 2048, 2304, 3072, 4096, 4608, 6144, 8192)
ETA_VALUES = (1, 2, 3, 4, 5, 6, 8)
UNIFORM_RADII = (1, 2, 3, 4, 5, 6, 8)
LWR_COMPRESSION_MODULI = (2, 3, 4, 5, 8, 16, 32, 64, 128, 256, 512, 1024)
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
SPARSE_TERNARY_FAST_SCREEN_PENALTY_BITS = 30.0
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
SUPPORTED_SECURITY_MODELS = {"classical", "quantum"}
SUPPORTED_RED_COST_MODELS = {"matzov", "adps16"}
SUPPORTED_RING_FAMILIES = {"power2", "ternary"}
SUPPORTED_DISTRIBUTION_SELECTORS = {"auto", "centered_binomial", "sparse_ternary"}
SUPPORTED_HARD_PROBLEMS = {
    "ntru": {"matrix", "ring"},
    "lwe": {"lwe", "rlwe", "lwr", "rlwr", "mlwe", "mlwr"},
    "sis": {"sis", "msis"},
}
LWR_VARIANTS = {"lwr", "rlwr", "mlwr"}
NTT_UNFRIENDLY_SCALE_POWER = 6
MAX_STRUCTURED_NTT_SCALE_POWER = 5
VALIDATION_CONFIG_ERROR_CODES = {
    "sage_not_found",
    "standard_estimator_not_configured",
    "enhanced_estimator_not_configured",
    "estimator_path_invalid",
    "estimator_origin_mismatch",
}
ESTIMATOR_MODELS = ("matzov", "adps16")
ESTIMATOR_MODES = ("classical", "quantum")
INVALID_ESTIMATOR_RESPONSE_CODE = "invalid_estimator_response"
MAX_SECURITY_BITS = 1_000_000.0


@dataclass(frozen=True)
class RequestOptions:
    target_security: int = 128
    hard_problem_category: str = "lwe"
    hard_problem_variant: str = "rlwe"
    ring_family: str = "power2"
    security_model: str = "classical"
    red_cost_model: str = "matzov"
    ntt_scale_power: int = 0
    min_q_bits: int = 2
    max_q_bits: int = 24
    min_n: int = 512
    max_n: int = 8192
    distribution: str = "auto"
    secret_distribution: str = "auto"
    error_distribution: str = "auto"
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


def recommend_rlwe(raw: dict[str, Any] | None = None, config: AppConfig | None = None) -> dict[str, Any]:
    """Return an RLWE recommendation and a small list of alternatives."""
    config = config or load_config()
    request = parse_request(raw or {}, config=config)
    started = time.perf_counter()

    raw_candidates = build_candidates(request)
    candidates = select_best_distribution_per_modulus(raw_candidates, request)
    if not candidates:
        raise ValueError("No candidates could be generated for the requested bounds.")

    viable = [c for c in candidates if meets_target(c["security"], request)]
    ranked = sorted(viable or candidates, key=lambda c: candidate_rank(c, request))

    profile = estimator_profile_for(request.hard_problem_category, request.hard_problem_variant)
    eligible_candidates: dict[str, dict[str, Any]] = {}
    for candidate in raw_candidates:
        eligible_candidates.setdefault(validation_candidate_key(candidate), candidate)
    estimator_result = None
    validation = validation_result(
        requested=False,
        profile=profile,
        attempted=0,
        successful=0,
        covered=0,
        eligible=len(eligible_candidates),
        attacks_complete=True,
    )
    validation_codes: list[str] = []
    failure_messages: list[str] = []
    raw_unknown_messages: list[str] = []
    if request.use_estimator:
        estimator_result = {"ok": True, "profile": profile, "validated": []}
        validated_candidates: list[dict[str, Any]] = []
        covered_keys: set[str] = set()
        max_validation_attempts = min(len(eligible_candidates), request.validation_attempts)
        validation_pool = rotate_secret_candidates(
            list(eligible_candidates.values()),
            lambda candidate: estimator_candidate_rank(candidate, request),
        )
        attempts = 0
        successful = 0
        attacks_complete = True
        estimator_commit = None
        for candidate in validation_pool[:max_validation_attempts]:
            attempts += 1
            raw_result = run_sage_estimator(
                candidate,
                request.estimator_timeout,
                config=config,
                request=request,
                profile=profile,
            )
            result, validation_entry = normalize_estimator_response(
                raw_result,
                request=request,
                expected_profile=profile,
            )
            estimator_result["validated"].append(validation_entry)
            if result is not None:
                estimator_commit = estimator_commit or result.get("estimator_commit")
                successful += 1
                covered_keys.add(validation_candidate_key(candidate))
                attacks_complete = attacks_complete and result["complete"]
                apply_estimator_result(candidate, result, request, profile=profile)
                validated_candidates.append(candidate)
                validation_codes.append("validation_applied")
                if not result["complete"]:
                    estimator_result["ok"] = False
                    validation_codes.append("validation_partial_attacks")
            else:
                estimator_result["ok"] = False
                code = validation_entry.get("code")
                message = validation_entry["message"]
                failure_messages.append(message)
                candidate["warnings"].append(message)
                if isinstance(code, str) and code in VALIDATION_CONFIG_ERROR_CODES:
                    validation_codes.append("validation_config_missing")
                else:
                    raw_unknown_messages.append(message)

        validation = validation_result(
            requested=True,
            profile=profile,
            attempted=attempts,
            successful=successful,
            covered=len(covered_keys),
            eligible=len(eligible_candidates),
            attacks_complete=attacks_complete,
            estimator_commit=estimator_commit,
            message_codes=validation_codes,
        )
        if raw_unknown_messages:
            validation["message"] = raw_unknown_messages[0]
            validation["messages"] = list(dict.fromkeys(raw_unknown_messages))
        if validated_candidates:
            ranked = sorted(validated_candidates, key=lambda c: validated_candidate_rank(c, request))
            viable = [candidate for candidate in ranked if meets_target(candidate["security"], request)]
        else:
            viable = [c for c in ranked if meets_target(c["security"], request)]
            ranked = sorted(viable or ranked, key=lambda c: candidate_rank(c, request))

    recommendation = ranked[0]
    alternatives = ranked[1:5]
    for candidate in [recommendation, *alternatives]:
        candidate["warning_codes"] = list(
            dict.fromkeys(candidate.get("warning_codes", []) + validation["message_codes"])
        )
        candidate["warnings"] = list(dict.fromkeys(candidate["warnings"] + failure_messages))

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return {
        "request": asdict(request),
        "recommendation": recommendation,
        "alternatives": alternatives,
        "estimator": estimator_result,
        "validation": validation,
        "search": {
            "elapsed_ms": elapsed_ms,
            "generated_candidates": len(raw_candidates),
            "modulus_candidates": len(candidates),
            "viable_candidates": len(viable),
            "strategy": [
                f"ring family first: {request.ring_family}",
                "degree n second",
                ntt_search_strategy_text(request),
                distribution_strategy_text(request),
                f"fast {request.red_cost_model.upper()} screen before optional Sage validation",
            ],
        },
        "next_question": (
            "Do you accept this RLWE instance? To bind it to a concrete encryption or signature scheme, "
            "the next step is to add scheme-specific constraints such as correctness, rejection sampling times, "
            "and smoothing parameters."
        ),
        "next_step_code": "bind_scheme_constraints",
    }


def parse_request(raw: dict[str, Any], config: AppConfig | None = None) -> RequestOptions:
    target = int(raw.get("target_security", raw.get("targetSecurity", 128)))
    if target < 40 or target > 512:
        raise ValueError("target_security must be between 40 and 512 bits.")

    hard_problem_category, hard_problem_variant = parse_hard_problem(raw)
    if hard_problem_category != "lwe":
        raise ValueError("LWE parameter search requires hard_problem_category=lwe.")

    model = str(raw.get("security_model", raw.get("securityModel", "classical"))).lower()
    if model not in SUPPORTED_SECURITY_MODELS:
        raise ValueError("security_model must be one of classical, quantum.")

    ring_family = str(raw.get("ring_family", raw.get("ringFamily", "power2"))).lower()
    if ring_family not in SUPPORTED_RING_FAMILIES:
        raise ValueError("ring_family must be one of power2, ternary.")

    red_cost_model = str(raw.get("red_cost_model", raw.get("redCostModel", "matzov"))).lower()
    if red_cost_model not in SUPPORTED_RED_COST_MODELS:
        raise ValueError("red_cost_model must be one of matzov, adps16.")

    ntt_scale_power = int(raw.get("ntt_scale_power", raw.get("nttScalePower", 0)))
    if ntt_scale_power < -1 or ntt_scale_power > NTT_UNFRIENDLY_SCALE_POWER:
        raise ValueError("ntt_scale_power must be between -1 and 6.")

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

    distribution = parse_distribution_selector(raw.get("distribution", "auto"), "distribution")
    secret_distribution = parse_distribution_selector(
        raw.get("secret_distribution", raw.get("secretDistribution", distribution)),
        "secret_distribution",
    )
    if is_lwr_variant(hard_problem_variant):
        error_distribution = parse_lwr_error_selector(
            raw.get(
                "error_distribution",
                raw.get("errorDistribution", raw.get("compressionP", raw.get("p", "3"))),
            )
        )
    else:
        error_distribution = parse_distribution_selector(
            raw.get("error_distribution", raw.get("errorDistribution", distribution)),
            "error_distribution",
        )

    estimator_timeout = raw.get(
        "estimator_timeout",
        raw.get("estimatorTimeout", (config or load_config()).estimator.default_timeout_seconds),
    )
    validation_count = max(1, min(12, int(raw.get("validation_count", raw.get("validationCount", 1)))))
    validation_attempts = raw.get(
        "validation_attempts",
        raw.get("validationAttempts", validation_count if validation_count == 1 else validation_count * 4),
    )

    return RequestOptions(
        target_security=target,
        hard_problem_category=hard_problem_category,
        hard_problem_variant=hard_problem_variant,
        ring_family=ring_family,
        security_model=model,
        red_cost_model=red_cost_model,
        ntt_scale_power=ntt_scale_power,
        min_q_bits=min_q_bits,
        max_q_bits=max_q_bits,
        min_n=min_n,
        max_n=max_n,
        distribution=distribution,
        secret_distribution=secret_distribution,
        error_distribution=error_distribution,
        use_estimator=bool(raw.get("use_estimator", raw.get("useEstimator", False))),
        estimator_timeout=max(4, min(300, int(estimator_timeout))),
        validation_count=validation_count,
        validation_attempts=max(validation_count, min(80, int(validation_attempts))),
    )


def parse_hard_problem(
    raw: dict[str, Any],
    default_category: str = "lwe",
    default_variant: str = "rlwe",
) -> tuple[str, str]:
    category = str(
        raw.get("hard_problem_category", raw.get("hardProblemCategory", default_category))
    ).lower()
    variant = str(
        raw.get("hard_problem_variant", raw.get("hardProblemVariant", default_variant))
    ).lower()

    if category not in SUPPORTED_HARD_PROBLEMS:
        allowed = ", ".join(name.upper() for name in SUPPORTED_HARD_PROBLEMS)
        raise ValueError(f"hard_problem_category must be one of {allowed}.")

    variants = SUPPORTED_HARD_PROBLEMS[category]
    if variant not in variants:
        allowed = ", ".join(sorted(variants))
        raise ValueError(f"hard_problem_variant for {category.upper()} must be one of {allowed}.")

    return category, variant


def parse_distribution_selector(value: Any, field: str) -> str:
    selector = str(value or "auto").lower()
    if selector == "uniform":
        raise ValueError(
            f"{field} must be one of auto, centered_binomial, sparse_ternary. "
            "Uniform is not a secret/error selector for LWE-style searches."
        )
    if selector not in SUPPORTED_DISTRIBUTION_SELECTORS:
        raise ValueError(f"{field} must be one of auto, centered_binomial, sparse_ternary.")
    return selector


def parse_lwr_error_selector(value: Any) -> str:
    selector = str(value or "3").strip().lower()
    if selector in {"auto", "uniform"}:
        return "3"
    if selector.startswith("p="):
        selector = selector[2:].strip()
    if selector.startswith("p"):
        selector = selector[1:].strip()
    try:
        p = int(selector)
    except ValueError as exc:
        raise ValueError("LWR-style error_distribution must be a compression modulus p.") from exc
    if p < 2:
        raise ValueError("LWR-style compression modulus p must be at least 2.")
    if p > max(LWR_COMPRESSION_MODULI):
        raise ValueError(f"LWR-style compression modulus p must be at most {max(LWR_COMPRESSION_MODULI)}.")
    return str(p)


def is_lwr_variant(variant: str) -> bool:
    return variant in LWR_VARIANTS


def build_candidates(request: RequestOptions) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    profile_cache: dict[tuple[int, int, str, int], dict[str, Any]] = {}
    security_cache: dict[tuple[int, int, float, float], dict[str, Any]] = {}
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
            for secret_distribution, error_distribution in distribution_pairs(n, q, request):
                candidate = make_candidate(
                    n=n,
                    q=q,
                    secret_distribution=secret_distribution,
                    error_distribution=error_distribution,
                    request=request,
                    profile_cache=profile_cache,
                    security_cache=security_cache,
                )
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


def make_candidate(
    n: int,
    q: int,
    secret_distribution: DistributionSpec,
    error_distribution: DistributionSpec,
    request: RequestOptions,
    profile_cache: dict[tuple[int, int, str, int], dict[str, Any]] | None = None,
    security_cache: dict[tuple[int, int, float, float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile_key = (n, q, request.ring_family, request.ntt_scale_power)
    if profile_cache is not None and profile_key in profile_cache:
        profile = profile_cache[profile_key]
    else:
        factors = factor_integer(q - 1)
        ring = ring_profile(n=n, q=q, family=request.ring_family)
        ntt = (
            ntt_unfriendly_profile(n=n, q=q, factors=factors, ring_family=request.ring_family)
            if ntt_scale_is_unrestricted(request.ntt_scale_power)
            else ntt_profile(n=n, q=q, factors=factors, ring_family=request.ring_family)
        )
        profile = {"factors": factors, "ring": ring, "ntt": ntt}
        if profile_cache is not None:
            profile_cache[profile_key] = profile
    factors = profile["factors"]
    ring = profile["ring"]
    ntt = profile["ntt"]
    sparse_penalty_bits = float(secret_distribution.estimator.get("fast_screen_penalty_bits", 0.0))
    security_key = (n, q, round(error_distribution.stddev, 12), sparse_penalty_bits)
    if security_cache is not None and security_key in security_cache:
        security = security_cache[security_key]
    else:
        security = fast_security_estimate(
            n=n,
            q=q,
            sigma=error_distribution.stddev,
            sparse_penalty_bits=sparse_penalty_bits,
        )
        if security_cache is not None:
            security_cache[security_key] = security
    warnings = [
        "This is an RLWE/LWE fast screen. It is not bound to a concrete scheme, so decryption error "
        "or rejection sampling times are not computed.",
    ]
    lwr_profile = lwr_rounding_profile(error_distribution) if is_lwr_variant(request.hard_problem_variant) else None
    candidate = {
        "ring": {
            **ring,
        },
        "modulus": {
            "q": q,
            "bits": modulus_bits(q),
            "prime": True,
            "q_minus_1_factorization": format_factorization(factors),
            "ntt_condition": ntt["condition"],
            "ntt_friendly": not ntt_scale_is_unrestricted(request.ntt_scale_power),
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
            "family": distribution_pair_family(secret_distribution, error_distribution),
            "name": distribution_pair_name(secret_distribution, error_distribution),
            "parameters": {
                "secret": secret_distribution.parameters,
                "error": error_distribution.parameters,
            },
            "secret": distribution_profile(secret_distribution),
            "error": distribution_profile(error_distribution),
            "estimator": {
                "secret": secret_distribution.estimator,
                "error": error_distribution.estimator,
            },
        },
        "security": security,
        "selection": {
            "target_security": request.target_security,
            "security_model": request.security_model,
            "selected_security_bits": selected_security_bits(security, request),
            "margin_bits": security_margin_bits(security, request),
            "meets_target": meets_target(security, request),
            "status": selection_status(meets_target(security, request)),
            "security_level": security_level_for_bits(selected_security_bits(security, request)),
            "rank_score": None,
        },
        "warnings": warnings,
        "warning_codes": ["screen_scheme_not_bound"],
    }
    if lwr_profile:
        candidate["lwr"] = lwr_profile
    candidate["visual_scores"] = visual_scores_for_candidate(candidate, request)
    return candidate


def distribution_pairs(n: int, q: int, request: RequestOptions) -> list[tuple[DistributionSpec, DistributionSpec]]:
    secret_candidates = distribution_candidates(n, request.secret_distribution)
    if not is_lwr_variant(request.hard_problem_variant):
        error_candidates = distribution_candidates(n, request.error_distribution)
        return list(product(secret_candidates, error_candidates))
    error_candidates = lwr_error_distribution_candidates(q, request)
    return list(product(secret_candidates, error_candidates))


def distribution_candidates(n: int, selector: str) -> list[DistributionSpec]:
    candidates: list[DistributionSpec] = []
    if selector in {"auto", "centered_binomial"}:
        candidates.extend(centered_binomial_spec(eta) for eta in ETA_VALUES)
    if selector in {"auto", "sparse_ternary"}:
        for l0, l1 in SPARSE_TERNARY_PARAMETERS:
            spec = sparse_ternary_spec(n=n, l0=l0, l1=l1)
            if spec.estimator["plus_weight"] >= 1 and spec.estimator["minus_weight"] >= 1:
                candidates.append(spec)
    return candidates


def lwr_error_distribution_candidates(q: int, request: RequestOptions) -> list[DistributionSpec]:
    p_values = [int(request.error_distribution)]
    return [compression_noise_spec(q, p) for p in p_values if p < q]


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


def uniform_spec(radius: int) -> DistributionSpec:
    variance = radius * (radius + 1) / 3.0
    return DistributionSpec(
        family="uniform",
        name=f"Uniform(-{radius},{radius})",
        parameters={"lower_bound": -radius, "upper_bound": radius},
        mean=0.0,
        variance=variance,
        stddev=math.sqrt(variance),
        support=[-radius, radius],
        symmetric=True,
        sampling=f"uniform integer coefficients in [-{radius}, {radius}]",
        estimator={"type": "uniform", "lower_bound": -radius, "upper_bound": radius},
    )


def compression_noise_spec(q: int, p: int) -> DistributionSpec:
    profile = compression_noise_profile(q=q, p=p)
    return DistributionSpec(
        family="compression_noise",
        name=f"CompressNoise(p={p})",
        parameters={"q": q, "p": p, "mean_shift": profile.mean_shift},
        mean=profile.mean,
        variance=profile.variance,
        stddev=profile.stddev,
        support=profile.support,
        symmetric=False,
        sampling="deterministic LWR-style compression noise induced by q -> p",
        estimator={
            "type": "compression_noise",
            "q": q,
            "p": p,
            "mean": profile.mean,
            "stddev": profile.stddev,
            "bounds": profile.support,
            "density": profile.density,
            "mean_shift": profile.mean_shift,
        },
    )


def sparse_ternary_spec(n: int, l0: int, l1: int) -> DistributionSpec:
    probability_each = ((2**l0) - 1) / (2 ** (2 * l0 + l1))
    variance = 2 * probability_each
    nonzero_probability = 2 * probability_each
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
            "nonzero_probability": nonzero_probability,
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
            "fast_screen_penalty_bits": sparse_ternary_fast_screen_penalty_bits(probability_each),
        },
    )


def sparse_ternary_fast_screen_penalty_bits(probability_each: float) -> float:
    if probability_each >= 0.25:
        return 0.0
    return SPARSE_TERNARY_FAST_SCREEN_PENALTY_BITS


def distribution_profile(distribution: DistributionSpec) -> dict[str, Any]:
    return {
        "family": distribution.family,
        "name": distribution.name,
        "mean": distribution.mean,
        "variance": round(distribution.variance, 9),
        "stddev": round(distribution.stddev, 9),
        "support": distribution.support,
        "symmetric": distribution.symmetric,
        "sampling": distribution.sampling,
        "estimator": distribution.estimator,
    }


def distribution_pair_family(secret_distribution: DistributionSpec, error_distribution: DistributionSpec) -> str:
    if secret_distribution.family == error_distribution.family:
        return secret_distribution.family
    return f"{secret_distribution.family} / {error_distribution.family}"


def distribution_pair_name(secret_distribution: DistributionSpec, error_distribution: DistributionSpec) -> str:
    if secret_distribution.name == error_distribution.name:
        return secret_distribution.name
    return f"Xs={secret_distribution.name}, Xe={error_distribution.name}"


def lwr_rounding_profile(error_distribution: DistributionSpec) -> dict[str, Any]:
    estimator = error_distribution.estimator
    if estimator.get("type") == "compression_noise":
        return {
            "p": int(estimator["p"]),
            "rounding_modulus": int(estimator["p"]),
            "error_distribution": error_distribution.name,
            "error_support": error_distribution.support,
            "mean": round(error_distribution.mean, 9),
            "stddev": round(error_distribution.stddev, 9),
            "note": "p is the compression modulus; error is the q -> p compression-noise law.",
        }
    lower_bound = int(estimator["lower_bound"])
    upper_bound = int(estimator["upper_bound"])
    p = upper_bound - lower_bound + 1
    return {
        "p": p,
        "rounding_modulus": p,
        "error_distribution": error_distribution.name,
        "error_support": [lower_bound, upper_bound],
        "note": "legacy profile: p is derived from the uniform error support size.",
    }


def fast_security_estimate(n: int, q: int, sigma: float, sparse_penalty_bits: float = 0.0) -> dict[str, Any]:
    beta = estimate_bkz_beta(n=n, q=q, sigma=sigma)
    classical = floor_bits(max(0.0, 0.292 * beta - sparse_penalty_bits))
    quantum = floor_bits(max(0.0, 0.265 * beta - sparse_penalty_bits))
    return {
        "source": "fast-screen",
        "source_code": "fast_screen",
        "classical_bits": classical,
        "quantum_bits": quantum,
        "matzov_bits": classical,
        "matzov_quantum_bits": quantum,
        "adps16_core_svp_bits": classical,
        "adps16_quantum_bits": quantum,
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
    classical, quantum = security_bits_for_reduction_model(security, request.red_cost_model)
    if request.security_model == "classical":
        return classical
    if request.security_model == "quantum":
        return quantum
    raise ValueError("security_model must be one of classical, quantum.")


def security_bits_for_reduction_model(security: dict[str, Any], red_cost_model: str) -> tuple[float, float]:
    if red_cost_model == "adps16":
        classical = parse_security_bits(security.get("adps16_core_svp_bits"))
        quantum = parse_security_bits(security.get("adps16_quantum_bits"))
    elif red_cost_model == "matzov":
        classical = parse_security_bits(security.get("matzov_bits"))
        quantum = parse_security_bits(security.get("matzov_quantum_bits"))
    else:
        classical = parse_security_bits(security.get("classical_bits"))
        quantum = parse_security_bits(security.get("quantum_bits"))
    return (
        classical if classical is not None else float("-inf"),
        quantum if quantum is not None else float("-inf"),
    )


def parse_security_bits(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        bits = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(bits) or bits < 0 or bits > MAX_SECURITY_BITS:
        return None
    return bits


def security_margin_bits(security: dict[str, Any], request: RequestOptions) -> float:
    selected = selected_security_bits(security, request)
    if not math.isfinite(selected):
        return float("-inf")
    return round(selected - request.target_security, 3)


def security_level_for_bits(bits: float | int | None) -> str:
    if bits is None:
        return "unclassified"
    value = float(bits)
    if value < 128:
        return "below NIST-I"
    if value < 192:
        return "NIST-I"
    if value < 256:
        return "NIST-III"
    return "NIST-V"


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
    candidate["selection"]["meets_target"] = meets_target(candidate["security"], request)
    candidate["selection"]["status"] = selection_status(candidate["selection"]["meets_target"])
    candidate["selection"]["security_level"] = security_level_for_bits(candidate["selection"]["selected_security_bits"])
    candidate["selection"]["rank_score"] = rank
    return rank


def distribution_rank(candidate: dict[str, Any], request: RequestOptions) -> tuple[float, ...]:
    margin = security_margin_bits(candidate["security"], request)
    stddev = float(candidate["distribution"]["secret"]["stddev"])
    error_stddev = float(candidate["distribution"]["error"]["stddev"])
    family_rank = 0 if candidate["distribution"]["secret"].get("family") == "sparse_ternary" else 1
    overkill = max(0.0, margin)
    shortage = abs(min(0.0, margin)) * 10_000.0
    if is_lwr_variant(request.hard_problem_variant):
        return (shortage, error_stddev, overkill, stddev, family_rank)
    return (shortage, overkill, error_stddev, stddev, family_rank)


def visual_scores_for_candidate(candidate: dict[str, Any], request: RequestOptions) -> dict[str, Any]:
    selected_bits = floor_bits(float(candidate["selection"]["selected_security_bits"]))
    compactness = compactness_profile(int(candidate["modulus"]["q"]), request)
    performance = performance_profile(
        n=int(candidate["ring"]["n"]),
        q=int(candidate["modulus"]["q"]),
        unrestricted=ntt_scale_is_unrestricted(request.ntt_scale_power),
    )
    return {
        "security": {
            "label": "Security",
            "score": clamp_score(selected_bits / 512.0),
            "bits": selected_bits,
            "max_bits": 512,
        },
        "compactness": compactness,
        "performance": performance,
    }


def compactness_profile(q: int, request: RequestOptions) -> dict[str, Any]:
    q_bits = modulus_bits(q)
    span = max(1, request.max_q_bits - request.min_q_bits)
    score = 1.0 - ((q_bits - request.min_q_bits) / span)
    return {
        "label": "Compactness",
        "score": clamp_score(score),
        "q": q,
        "q_bits": q_bits,
        "min_q_bits": request.min_q_bits,
        "max_q_bits": request.max_q_bits,
    }


def performance_profile(n: int, q: int, unrestricted: bool = False) -> dict[str, Any]:
    if unrestricted:
        return {
            "label": "Performance",
            "score": 0.0,
            "k": None,
            "k_label": "lift",
            "divisor": None,
            "condition": "no restriction of n and q (NTT unfriendly)",
        }

    q_minus_1 = q - 1
    scales = [
        (2 * n, 0.5, "2n"),
        (n, 1.0, "n"),
    ]
    k = 2
    divisor = max(1, n // 2)
    while divisor >= 1:
        scales.append((divisor, float(k), f"n/{k}"))
        k *= 2
        divisor = n // k

    for divisor, scale_k, label in scales:
        if divisor > 0 and q_minus_1 % divisor == 0:
            return {
                "label": "Performance",
                "score": 1.0 if scale_k <= 1.0 else clamp_score(1.0 / scale_k),
                "k": scale_k,
                "k_label": "1/2" if scale_k == 0.5 else format_scale_number(scale_k),
                "divisor": divisor,
                "condition": f"{label} | q - 1",
            }

    return {
        "label": "Performance",
        "score": 0.0,
        "k": None,
        "k_label": "n/a",
        "divisor": None,
        "condition": "no power-of-two n/k divisor found",
    }


def update_visual_security(candidate: dict[str, Any]) -> None:
    profile = candidate.get("visual_scores")
    if not profile:
        return
    selected_bits = floor_bits(float(candidate["selection"]["selected_security_bits"]))
    profile["security"]["bits"] = selected_bits
    profile["security"]["score"] = clamp_score(selected_bits / 512.0)


def clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def format_scale_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)


def estimator_candidate_rank(candidate: dict[str, Any], request: RequestOptions) -> tuple[float, ...]:
    ring_rank = 0 if candidate["ring"]["family_id"] == request.ring_family else 1
    n = int(candidate["ring"]["n"])
    q = int(candidate["modulus"]["q"])
    return (ring_rank, n, q, *distribution_rank(candidate, request))


def secret_validation_key(candidate: dict[str, Any]) -> str:
    estimator = candidate["distribution"]["secret"]["estimator"]
    return json.dumps(estimator, sort_keys=True, separators=(",", ":"))


def validation_candidate_key(candidate: dict[str, Any]) -> str:
    descriptor = {
        "ring_family": candidate["ring"]["family_id"],
        "ring_degree": candidate["ring"]["n"],
        "q": candidate["modulus"]["q"],
        "secret": candidate["distribution"]["secret"]["estimator"],
        "error": candidate["distribution"]["error"]["estimator"],
    }
    return json.dumps(descriptor, sort_keys=True, separators=(",", ":"))


def rotate_secret_candidates(candidates, rank_key):
    buckets: dict[str, list[dict[str, Any]]] = {}
    for candidate in sorted(candidates, key=rank_key):
        buckets.setdefault(secret_validation_key(candidate), []).append(candidate)

    ordered: list[dict[str, Any]] = []
    while buckets:
        for key in list(buckets):
            ordered.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
    return ordered


def validated_candidate_rank(candidate: dict[str, Any], request: RequestOptions) -> tuple[Any, ...]:
    selected_bits = selected_security_bits(candidate["security"], request)
    measured_rank = -selected_bits if math.isfinite(selected_bits) else float("inf")
    meets = meets_target(candidate["security"], request)
    ring_rank = 0 if candidate["ring"]["family_id"] == request.ring_family else 1
    n = int(candidate["ring"]["n"])
    q = int(candidate["modulus"]["q"])
    secret = candidate["distribution"]["secret"]
    error = candidate["distribution"]["error"]
    sampling_rank = 0 if secret.get("family") == "sparse_ternary" else 1
    rank = (
        0 if meets else 1,
        measured_rank,
        ring_rank,
        n,
        modulus_bits(q),
        q,
        sampling_rank,
        str(secret.get("sampling", "")),
        str(error.get("sampling", "")),
        float(secret["stddev"]),
        float(error["stddev"]),
        str(secret.get("name", "")),
        str(error.get("name", "")),
    )
    candidate["selection"]["selected_security_bits"] = selected_bits
    candidate["selection"]["margin_bits"] = security_margin_bits(candidate["security"], request)
    candidate["selection"]["meets_target"] = meets
    candidate["selection"]["status"] = selection_status(meets)
    candidate["selection"]["security_level"] = security_level_for_bits(selected_bits)
    candidate["selection"]["rank_score"] = rank
    return rank


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
    if ntt_scale_is_unrestricted(ntt_scale_power):
        return unrestricted_prime_candidates(
            min_q_bits=min_q_bits,
            max_q_bits=max_q_bits,
            limit=limit,
        )

    modulus = ntt_divisor(n, ntt_scale_power, ring_family)
    found = {
        q
        for q in COMMON_NTT_PRIMES
        if min_q_bits <= modulus_bits(q) <= max_q_bits
        and q > modulus
        and (q - 1) % modulus == 0
        and is_prime(q)
    }

    min_bits = max(min_q_bits, modulus_bits(modulus + 1))
    bit_targets = range(min_bits, max_q_bits + 1)
    for bits in bit_targets:
        start_k = max(1, ((1 << (bits - 1)) - 1) // modulus)
        stop_k = max(start_k + 1, ((1 << bits) - 1) // modulus)
        step = max(1, (stop_k - start_k) // 5000)
        added_for_bits = 0
        for k in range(start_k, stop_k + 1, step):
            q = k * modulus + 1
            if not min_q_bits <= modulus_bits(q) <= max_q_bits:
                continue
            if is_prime(q):
                found.add(q)
                added_for_bits += 1
                if added_for_bits >= 10:
                    break

    scored = sorted(found, key=lambda q: (q, -ntt_profile(n, q, factor_integer(q - 1), ring_family)["score"]))
    return scored[:limit]


def unrestricted_prime_candidates(min_q_bits: int, max_q_bits: int, limit: int = 96) -> list[int]:
    found: set[int] = set()
    for q in COMMON_NTT_PRIMES:
        if min_q_bits <= modulus_bits(q) <= max_q_bits and is_prime(q):
            found.add(q)

    lower_bound = max(3, 1 << (min_q_bits - 1))
    upper_bound = (1 << max_q_bits) - 1
    low = lower_bound
    high = upper_bound
    while low <= high and len(found) < limit:
        midpoint = (low + high) // 2
        q = next_prime_at_or_above(midpoint)
        if q <= upper_bound:
            found.add(q)
        high = midpoint - 1

    for bits in range(min_q_bits, max_q_bits + 1):
        lower = max(3, 1 << (bits - 1))
        upper = (1 << bits) - 1
        q = next_prime_at_or_above(lower)
        added_for_bits = 0
        while q <= upper and added_for_bits < 4:
            found.add(q)
            added_for_bits += 1
            q = next_prime_at_or_above(q + 1)

    return sorted(found)[:limit]


def next_prime_at_or_above(value: int) -> int:
    candidate = max(2, value)
    if candidate == 2:
        return candidate
    if candidate % 2 == 0:
        candidate += 1
    while not is_prime(candidate):
        candidate += 2
    return candidate


def ntt_scale_is_unrestricted(ntt_scale_power: int) -> bool:
    return ntt_scale_power == NTT_UNFRIENDLY_SCALE_POWER


def ntt_divisor(n: int, ntt_scale_power: int, ring_family: str = "power2") -> int:
    base = 3 * n if ring_family == "ternary" else n
    if ntt_scale_power < 0:
        return 2 * base
    return max(1, base // (2**ntt_scale_power))


def ntt_requirement_label(ntt_scale_power: int, ring_family: str = "power2") -> str:
    if ntt_scale_is_unrestricted(ntt_scale_power):
        return "no restriction of n and q (NTT unfriendly)"
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


def ntt_search_strategy_text(request: RequestOptions) -> str:
    label = ntt_requirement_label(request.ntt_scale_power, request.ring_family)
    if ntt_scale_is_unrestricted(request.ntt_scale_power):
        return "prime q with no n/q divisibility restriction; use lift-based NTT and prefer the smallest q"
    return f"prime q with {label} | q-1"


def distribution_strategy_text(request: RequestOptions) -> str:
    if is_lwr_variant(request.hard_problem_variant):
        return f"choose secret distribution after q; LWR error uses q->p compression noise with p={request.error_distribution}"
    return "choose secret and error distributions independently after q"


def ntt_profile(n: int, q: int, factors: dict[int, int] | None = None, ring_family: str = "power2") -> dict[str, Any]:
    factors = factors or factor_integer(q - 1)
    two_adicity = factors.get(2, 0)
    three_adicity = factors.get(3, 0)
    if ring_family == "ternary":
        return ternary_ntt_profile(n, q, factors, two_adicity, three_adicity)

    return power2_ntt_profile(n, q, factors, two_adicity)


def ntt_unfriendly_profile(n: int, q: int, factors: dict[int, int], ring_family: str = "power2") -> dict[str, Any]:
    return {
        "condition": "no restriction of n and q (NTT unfriendly)",
        "quality": "lift_ntt_unfriendly",
        "layers_remaining": 99,
        "factor_count": 0,
        "factor_degree": 0,
        "polynomial_factorization": "lift-based NTT mode; q is not required to split the ring polynomial",
        "two_adicity": factors.get(2, 0),
        "three_adicity": factors.get(3, 0),
        "small_factor_weight": sum(exp for prime, exp in factors.items() if prime <= 31),
        "score": 0,
    }


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


def invalid_estimator_response(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "code": INVALID_ESTIMATOR_RESPONSE_CODE,
        "message": f"Invalid estimator response: {message}",
    }


def normalize_mode_results(modes: Any, context: str) -> dict[str, dict[str, Any]]:
    if not isinstance(modes, dict):
        raise ValueError(f"{context} must be an object")

    normalized: dict[str, dict[str, Any]] = {}
    for mode, mode_result in modes.items():
        if not isinstance(mode_result, dict):
            raise ValueError(f"{context}.{mode} must be an object")
        if type(mode_result.get("ok")) is not bool:
            raise ValueError(f"{context}.{mode}.ok must be a boolean")
        if type(mode_result.get("complete")) is not bool:
            raise ValueError(f"{context}.{mode}.complete must be a boolean")

        normalized_mode = dict(mode_result)
        if "min_bits" in mode_result:
            bits = parse_security_bits(mode_result["min_bits"])
            if bits is None:
                raise ValueError(
                    f"{context}.{mode}.min_bits must be between 0 and "
                    f"{int(MAX_SECURITY_BITS)}"
                )
            normalized_mode["min_bits"] = bits
        elif mode_result["ok"]:
            raise ValueError(f"{context}.{mode}.min_bits is required when ok is true")
        normalized[str(mode)] = normalized_mode
    return normalized


def normalize_estimator_response(
    response: Any,
    request: RequestOptions,
    expected_profile: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not isinstance(response, dict):
        failure = invalid_estimator_response("expected an object")
        return None, failure

    machine_code = response.get("code")
    if machine_code is not None and not isinstance(machine_code, str):
        return None, invalid_estimator_response("code must be a string or null")

    has_structured_results = "models" in response or "modes" in response
    if response.get("ok") is False and not has_structured_results:
        failure = dict(response)
        message = failure.get("message")
        if not isinstance(message, str) or not message:
            message = "Sage/lattice-estimator could not estimate this candidate."
        failure["message"] = message
        return None, failure

    if type(response.get("ok")) is not bool:
        return None, invalid_estimator_response("ok must be a boolean")
    if type(response.get("complete")) is not bool:
        return None, invalid_estimator_response("complete must be a boolean")
    if response.get("estimator_profile") != expected_profile:
        return None, invalid_estimator_response(
            f"estimator_profile must be {expected_profile}"
        )
    estimator_commit = response.get("estimator_commit")
    if estimator_commit is not None and not isinstance(estimator_commit, str):
        return None, invalid_estimator_response("estimator_commit must be a string or null")

    models = response.get("models")
    modes = response.get("modes")
    if not isinstance(models, dict):
        return None, invalid_estimator_response("models must be an object")

    try:
        normalized_models = {
            str(model): normalize_mode_results(model_modes, f"models.{model}")
            for model, model_modes in models.items()
        }
        normalized_modes = normalize_mode_results(modes, "modes")
    except ValueError as exc:
        return None, invalid_estimator_response(str(exc))

    selected_mode = normalized_models.get(request.red_cost_model, {}).get(
        request.security_model,
        {},
    )
    if selected_mode.get("ok") is not True:
        return None, invalid_estimator_response(
            f"no finite {request.red_cost_model}/{request.security_model} security estimate was returned"
        )

    all_modes_complete = all(
        normalized_models.get(model, {}).get(mode, {}).get("ok")
        and normalized_models[model][mode]["complete"]
        for model in ESTIMATOR_MODELS
        for mode in ESTIMATOR_MODES
    )
    normalized = dict(response)
    normalized["ok"] = True
    normalized["complete"] = bool(
        response["ok"] and response["complete"] and all_modes_complete
    )
    normalized["models"] = normalized_models
    normalized["modes"] = normalized_modes
    return normalized, response


def run_sage_estimator(
    candidate: dict[str, Any],
    timeout: int,
    config: AppConfig | None = None,
    request: RequestOptions | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    config = config or load_config()
    category = request.hard_problem_category if request else "lwe"
    variant = request.hard_problem_variant if request else "rlwe"
    profile = profile or estimator_profile_for(category, variant)
    payload = {
        "problem": "lwe",
        "n": candidate["ring"]["n"],
        "q": candidate["modulus"]["q"],
        "distribution": candidate["distribution"],
        "secret_distribution": candidate["distribution"]["secret"],
        "error_distribution": candidate["distribution"]["error"],
        "hard_problem_variant": variant,
        "ring_degree": candidate["ring"]["n"],
        "per_attack_timeout": max(3, min(90, config.estimator.per_attack_timeout_seconds or timeout // 2)),
    }
    return run_estimator(payload, timeout, config, profile)


def apply_estimator_result(
    candidate: dict[str, Any],
    estimator_result: dict[str, Any],
    request: RequestOptions,
    profile: str | None = None,
) -> None:
    resolved_profile = profile or estimator_result.get("estimator_profile")
    if not resolved_profile:
        resolved_profile = estimator_profile_for(
            request.hard_problem_category,
            request.hard_problem_variant,
        )
    adps16_classical = estimator_model_bits(estimator_result, "adps16", "classical")
    adps16_quantum = estimator_model_bits(estimator_result, "adps16", "quantum")
    matzov_classical = estimator_model_bits(estimator_result, "matzov", "classical")
    matzov_quantum = estimator_model_bits(estimator_result, "matzov", "quantum")
    classical_bits = adps16_classical if adps16_classical is not None else matzov_classical
    quantum_bits = adps16_quantum if adps16_quantum is not None else matzov_quantum
    candidate["security"] = {
        "source": "sage-lattice-estimator",
        "source_code": f"sage_{resolved_profile}",
        "classical_bits": floor_optional_bits(classical_bits),
        "quantum_bits": floor_optional_bits(quantum_bits),
        "matzov_bits": floor_optional_bits(matzov_classical),
        "matzov_quantum_bits": floor_optional_bits(matzov_quantum),
        "adps16_core_svp_bits": floor_optional_bits(adps16_classical),
        "adps16_quantum_bits": floor_optional_bits(adps16_quantum),
        "attacks": estimator_result.get("models") or estimator_result.get("modes", {}),
        "estimator_commit": estimator_result.get("estimator_commit"),
        "notes": [
            "Estimated as an LWE instance with n RLWE samples; use full scheme analysis for production.",
            "Available MATZOV and ADPS16 mode estimates are reported without fast-screen substitution.",
        ],
    }
    candidate["selection"]["selected_security_bits"] = selected_security_bits(candidate["security"], request)
    candidate["selection"]["margin_bits"] = security_margin_bits(candidate["security"], request)
    candidate["selection"]["meets_target"] = meets_target(candidate["security"], request)
    candidate["selection"]["status"] = selection_status(candidate["selection"]["meets_target"])
    candidate["selection"]["security_level"] = security_level_for_bits(candidate["selection"]["selected_security_bits"])
    update_visual_security(candidate)
    candidate["warnings"].append("Sage/lattice-estimator rough validation was applied to this recommendation.")
    candidate["warning_codes"] = list(
        dict.fromkeys(candidate.get("warning_codes", []) + ["validation_applied"])
    )


def estimator_model_bits(estimator_result: dict[str, Any], model: str, mode: str) -> float | None:
    models = estimator_result.get("models")
    if not isinstance(models, dict):
        return None
    model_modes = models.get(model)
    if not isinstance(model_modes, dict):
        return None
    mode_result = model_modes.get(mode)
    if not isinstance(mode_result, dict) or mode_result.get("ok") is not True:
        return None
    return parse_security_bits(mode_result.get("min_bits"))


def floor_bits(value: float, digits: int = 1) -> float:
    bits = parse_security_bits(value)
    if bits is None:
        raise ValueError(
            f"security bits must be between 0 and {int(MAX_SECURITY_BITS)}"
        )
    scale = 10**digits
    return math.floor(bits * scale) / scale


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
