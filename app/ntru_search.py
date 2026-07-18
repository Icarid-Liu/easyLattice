from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from itertools import combinations_with_replacement
from typing import Any

from .config import AppConfig, load_config
from .estimator_process import run_estimator
from .parameter_search import (
    NTT_UNFRIENDLY_SCALE_POWER,
    SUPPORTED_RED_COST_MODELS,
    SUPPORTED_SECURITY_MODELS,
    VALIDATION_CONFIG_ERROR_CODES,
    compactness_profile,
    factor_integer,
    floor_bits,
    floor_optional_bits,
    format_factorization,
    is_prime,
    ntt_scale_is_unrestricted,
    normalize_estimator_response,
    parse_hard_problem,
    parse_security_bits,
    performance_profile,
    selected_security_bits,
    update_visual_security,
    security_level_for_bits,
)
from .security_result import modulus_bits, selection_status, validation_result


SUPPORTED_NTRU_RING_FAMILIES = {"auto", "power2", "hps", "hrss", "ntru_prime"}
NTRU_ESTIMATOR_PROFILE = "standard"
NTRU_ESTIMATOR_MODELS = ("matzov", "adps16")
NTRU_ESTIMATOR_MODES = ("classical", "quantum")
QUANTUM_ESTIMATE_UNAVAILABLE_CODE = "quantum_estimate_unavailable"
QUANTUM_ESTIMATE_UNAVAILABLE_MESSAGE = (
    "No quantum security estimate is available for this NTRU candidate."
)
SNTRUP_ROWS = (
    ("sntrup653", 653, 4621, 288, 129.0, 117.0, 1),
    ("sntrup761", 761, 4591, 286, 153.0, 139.0, 2),
    ("sntrup857", 857, 5167, 322, 175.0, 159.0, 3),
    ("sntrup953", 953, 6343, 396, 196.0, 178.0, 4),
    ("sntrup1013", 1013, 7177, 448, 209.0, 190.0, 4),
    ("sntrup1277", 1277, 7879, 492, 270.0, 245.0, 5),
)


@dataclass(frozen=True)
class NTRURequest:
    target_security: int = 128
    hard_problem_category: str = "ntru"
    hard_problem_variant: str = "ring"
    ring_family: str = "power2"
    security_model: str = "classical"
    red_cost_model: str = "matzov"
    ntt_scale_power: int = 1
    min_n: int = 256
    max_n: int = 2048
    min_q_bits: int = 2
    max_q_bits: int = 24
    distribution: str = "auto"
    use_estimator: bool = False
    estimator_timeout: int = 45
    validation_count: int = 3
    validation_attempts: int = 8


@dataclass(frozen=True)
class NTRUCandidateSpec:
    family_id: str
    n: int
    q: int
    polynomial: str
    quotient: str
    ntru_type: str
    secret_distribution: dict[str, Any]
    error_distribution: dict[str, Any]
    screen_bits: float
    screen_attack: str
    note: str
    preset: str | None = None
    fixed_weight: int | None = None
    screen_quantum_bits: float | None = None
    nist_category: int | None = None
    calibration: dict[str, Any] | None = None


def recommend_ntru(raw: dict[str, Any] | None = None, config: AppConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    request = parse_ntru_request(raw or {}, config=config)
    started = time.perf_counter()
    specs = ntru_candidate_specs(request)
    if not specs:
        raise ValueError("No NTRU candidates could be generated for the requested bounds.")

    raw_candidates = [make_ntru_candidate(spec, request) for spec in specs]
    candidates = select_best_distribution_per_modulus(raw_candidates, request)
    viable = [candidate for candidate in candidates if candidate["selection"]["meets_target"]]
    ranked = sorted(viable or candidates, key=lambda candidate: candidate_rank(candidate, request))

    eligible_candidates = {
        ntru_validation_candidate_key(candidate): candidate
        for candidate in candidates
    }
    estimator_result = None
    validation = validation_result(
        requested=False,
        profile=NTRU_ESTIMATOR_PROFILE,
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
        estimator_result = {
            "ok": True,
            "profile": NTRU_ESTIMATOR_PROFILE,
            "validated": [],
        }
        validated_candidates: list[dict[str, Any]] = []
        covered_keys: set[str] = set()
        attempts = 0
        successful = 0
        attacks_complete = True
        estimator_commit = None
        max_validation_attempts = min(len(eligible_candidates), request.validation_attempts)
        validation_pool = sorted(
            eligible_candidates.values(),
            key=lambda candidate: candidate_rank(candidate, request),
        )
        for candidate in validation_pool[:max_validation_attempts]:
            if successful >= request.validation_count:
                break
            attempts += 1
            raw_result = run_ntru_estimator(
                candidate,
                request.estimator_timeout,
                config=config,
                request=request,
            )
            result, validation_entry = normalize_ntru_estimator_response(
                raw_result,
                candidate=candidate,
                request=request,
            )
            estimator_result["validated"].append(validation_entry)
            if result is not None:
                estimator_commit = estimator_commit or result.get("estimator_commit")
                successful += 1
                covered_keys.add(ntru_validation_candidate_key(candidate))
                attacks_complete = attacks_complete and result["complete"]
                apply_ntru_estimator_result(candidate, result, request)
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
            profile=NTRU_ESTIMATOR_PROFILE,
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
            ranked = sorted(
                validated_candidates,
                key=lambda candidate: validated_ntru_candidate_rank(candidate, request),
            )
            viable = [
                candidate
                for candidate in ranked
                if candidate["selection"]["meets_target"]
            ]
        else:
            viable = [
                candidate
                for candidate in ranked
                if candidate["selection"]["meets_target"]
            ]
            ranked = sorted(
                viable or ranked,
                key=lambda candidate: candidate_rank(candidate, request),
            )

    recommendation = ranked[0]
    alternatives = ranked[1:5]
    if (
        request.security_model == "quantum"
        and recommendation["selection"]["selected_security_bits"] is None
    ):
        validation["message_codes"] = list(
            dict.fromkeys(
                validation["message_codes"] + [QUANTUM_ESTIMATE_UNAVAILABLE_CODE]
            )
        )
        validation.setdefault("message", QUANTUM_ESTIMATE_UNAVAILABLE_MESSAGE)
    for candidate in [recommendation, *alternatives]:
        candidate["warning_codes"] = list(
            dict.fromkeys(candidate.get("warning_codes", []) + validation["message_codes"])
        )
        candidate["warnings"] = list(
            dict.fromkeys(candidate["warnings"] + failure_messages)
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000)

    return {
        "request": asdict(request) | {"problem": "ntru"},
        "recommendation": recommendation,
        "alternatives": alternatives,
        "estimator": estimator_result,
        "validation": validation,
        "search": {
            "elapsed_ms": elapsed_ms,
            "generated_candidates": len(specs),
            "modulus_candidates": len(candidates),
            "viable_candidates": len(viable),
            "strategy": [
                f"ring family first: {request.ring_family}",
                "degree n second",
                ntru_ntt_strategy_text(request),
                "calibrate sigma with a discrete-Gaussian proxy after q",
                "choose the closest fast-sampling Xf/Xg distribution with stddev above that sigma",
                "rank only candidates above the requested lower bound",
            ],
        },
        "next_question": (
            "Do you accept this NTRU instance? To bind it to a concrete scheme such as NEV, DAWN, "
            "or BAT, the next step is to add encoding, correctness or failure-rate checks, key "
            "invertibility, and scheme-level security losses."
        ),
        "next_step_code": "bind_scheme_constraints",
    }


def parse_ntru_request(raw: dict[str, Any], config: AppConfig | None = None) -> NTRURequest:
    target = int(raw.get("target_security", raw.get("targetSecurity", 128)))
    if target < 40 or target > 512:
        raise ValueError("target_security must be between 40 and 512 bits.")

    hard_problem_category, hard_problem_variant = parse_hard_problem(raw, "ntru", "ring")
    if hard_problem_category != "ntru":
        raise ValueError("NTRU selector requires hard_problem_category=ntru.")

    family = str(raw.get("ring_family", raw.get("ringFamily", "power2"))).lower()
    if family not in SUPPORTED_NTRU_RING_FAMILIES:
        raise ValueError("NTRU ring_family must be one of auto, power2, hps, hrss, ntru_prime.")
    security_model = str(raw.get("security_model", raw.get("securityModel", "classical"))).lower()
    if security_model not in SUPPORTED_SECURITY_MODELS:
        raise ValueError("security_model must be one of classical, quantum.")
    red_cost_model = str(raw.get("red_cost_model", raw.get("redCostModel", "matzov"))).lower()
    if red_cost_model not in SUPPORTED_RED_COST_MODELS:
        raise ValueError("red_cost_model must be one of matzov, adps16.")

    min_q_bits = int(raw.get("min_q_bits", raw.get("minQBits", 2)))
    max_q_bits = int(raw.get("max_q_bits", raw.get("maxQBits", 24)))
    min_n = int(raw.get("min_n", raw.get("minN", 256)))
    max_n = int(raw.get("max_n", raw.get("maxN", 2048)))
    ntt_scale_power = int(raw.get("ntt_scale_power", raw.get("nttScalePower", 1)))
    if ntt_scale_power < -1 or ntt_scale_power > NTT_UNFRIENDLY_SCALE_POWER:
        raise ValueError("ntt_scale_power must be between -1 and 6.")
    config = config or load_config()
    estimator_timeout = int(
        raw.get(
            "estimator_timeout",
            raw.get("estimatorTimeout", config.estimator.default_timeout_seconds),
        )
    )
    validation_count = max(1, min(12, int(raw.get("validation_count", raw.get("validationCount", 3)))))
    validation_attempts = int(raw.get("validation_attempts", raw.get("validationAttempts", validation_count + 2)))

    use_estimator = bool(raw.get("use_estimator", raw.get("useEstimator", False)))

    return NTRURequest(
        target_security=target,
        hard_problem_category=hard_problem_category,
        hard_problem_variant=hard_problem_variant,
        ring_family=family,
        security_model=security_model,
        red_cost_model=red_cost_model,
        ntt_scale_power=ntt_scale_power,
        min_n=min_n,
        max_n=max_n,
        min_q_bits=min_q_bits,
        max_q_bits=max_q_bits,
        distribution=str(raw.get("distribution", "auto")),
        use_estimator=use_estimator,
        estimator_timeout=max(4, min(300, estimator_timeout)),
        validation_count=validation_count,
        validation_attempts=max(1, min(24, validation_attempts)),
    )


def ntru_candidate_specs(request: NTRURequest) -> list[NTRUCandidateSpec]:
    specs = []
    families = (
        ("power2", "hps", "hrss", "ntru_prime")
        if request.ring_family == "auto"
        else (request.ring_family,)
    )
    for family in families:
        if family == "power2":
            specs.extend(power2_specs())
        elif family == "hps":
            specs.extend(hps_specs())
        elif family == "hrss":
            specs.extend(hrss_specs())
        elif family == "ntru_prime":
            specs.extend(ntru_prime_specs())
    return [
        spec
        for spec in specs
        if request.min_n <= spec.n <= request.max_n
        and request.min_q_bits <= modulus_bits(spec.q) <= request.max_q_bits
        and ntru_satisfies_ntt_requirement(spec, request)
    ]


def power2_specs() -> list[NTRUCandidateSpec]:
    n = 512
    rows = [
        (257, 0.51, 128.0),
        (769, 0.78, 128.3),
        (3329, 1.60, 128.4),
        (7681, 2.6, 131.4),
        (10753, 3.0, 130.2),
        (11777, 3.0, 128.4),
        (12289, 3.1, 128.7),
        (12289, 4.0532, 140.4),
    ]
    specs = []
    for q, sigma, bits in rows:
        fast_distribution = closest_fast_distribution(n=n, sigma_lower_bound=sigma)
        specs.append(NTRUCandidateSpec(
            family_id="power2",
            n=n,
            q=q,
            polynomial=f"x^{n} + 1",
            quotient=f"Z_{q}[x] / (x^{n} + 1)",
            ntru_type="circulant",
            secret_distribution=fast_distribution,
            error_distribution=fast_distribution,
            screen_bits=bits,
            screen_attack="usvp",
            note="power-of-two cyclotomic NTRU candidate",
            calibration={
                "method": "gaussian_proxy_then_fast_distribution",
                "gaussian_proxy": gaussian_distribution(sigma),
                "gaussian_proxy_bits": bits,
                "sigma_lower_bound": sigma,
                "chosen_fast_distribution": fast_distribution["name"],
                "chosen_fast_stddev": fast_distribution["stddev"],
            },
        ))
    return specs


def hps_specs() -> list[NTRUCandidateSpec]:
    rows = [
        (593, 128.6),
        (599, 129.9),
        (607, 131.8),
        (677, 148.1),
    ]
    return [hps_spec(N, bits) for N, bits in rows]


def hrss_specs() -> list[NTRUCandidateSpec]:
    rows = [
        (673, 130.8),
        (677, 131.6),
        (683, 133.1),
        (701, 137.5),
    ]
    return [hrss_spec(N, bits) for N, bits in rows]


def ntru_prime_specs() -> list[NTRUCandidateSpec]:
    return [
        NTRUCandidateSpec(
            family_id="ntru_prime",
            preset=name,
            n=n,
            q=q,
            polynomial=f"x^{n} - x - 1",
            quotient=f"Z_{q}[x] / (x^{n} - x - 1)",
            ntru_type="circulant",
            secret_distribution=sparse_ternary_distribution(weight // 2, weight // 2, n),
            error_distribution=uniform_mod_distribution(3),
            fixed_weight=weight,
            screen_bits=classical_bits,
            screen_quantum_bits=quantum_bits,
            screen_attack="official-including-hybrid-minimum",
            nist_category=category,
            note=(
                "Streamlined NTRU Prime Round-3 preset; fixed-weight signs use a balanced "
                "estimator approximation."
            ),
        )
        for name, n, q, weight, classical_bits, quantum_bits, category in SNTRUP_ROWS
    ]


def hps_spec(N: int, bits: float) -> NTRUCandidateSpec:
    n = N - 1
    q = 2048
    return NTRUCandidateSpec(
        family_id="hps",
        n=n,
        q=q,
        polynomial=f"x^{N} - 1 with one relation removed by the estimator",
        quotient=f"NTRU-HPS style mod q={q}, public polynomial degree N={N}",
        ntru_type="circulant",
        secret_distribution=uniform_mod_distribution(3),
        error_distribution=sparse_ternary_distribution(127, 127, n),
        screen_bits=bits,
        screen_attack="bdd_hybrid",
        note="HPS-like NTRU candidate",
    )


def hrss_spec(N: int, bits: float) -> NTRUCandidateSpec:
    n = N - 1
    q = 8192
    return NTRUCandidateSpec(
        family_id="hrss",
        n=n,
        q=q,
        polynomial=f"x^{N} - 1 with one relation removed by the estimator",
        quotient=f"NTRU-HRSS style mod q={q}, public polynomial degree N={N}",
        ntru_type="circulant",
        secret_distribution=uniform_mod_distribution(3),
        error_distribution=uniform_mod_distribution(3),
        screen_bits=bits,
        screen_attack="usvp",
        note="HRSS-like NTRU candidate",
    )


def make_ntru_candidate(spec: NTRUCandidateSpec, request: NTRURequest) -> dict[str, Any]:
    ntru_type = estimator_ntru_type(request, spec)
    classical_bits = floor_bits(spec.screen_bits)
    quantum_bits = floor_optional_bits(spec.screen_quantum_bits)
    security = {
        "source": "ntru-reference-screen",
        "source_code": "ntru_reference_screen",
        "classical_bits": classical_bits,
        "quantum_bits": quantum_bits,
        "matzov_bits": classical_bits,
        "matzov_quantum_bits": quantum_bits,
        "adps16_core_svp_bits": classical_bits,
        "adps16_quantum_bits": quantum_bits,
        "ntru_bits": classical_bits,
        "reference_classical_bits": classical_bits,
        "reference_quantum_bits": quantum_bits,
        "reference_attack": spec.screen_attack,
        "nist_category": spec.nist_category,
        "reference_screen": {
            "classical_bits": classical_bits,
            "quantum_bits": quantum_bits,
            "attack": spec.screen_attack,
            "nist_category": spec.nist_category,
        },
        "attacks": {
            spec.screen_attack: {
                "ok": True,
                "rop_bits": classical_bits,
                "quantum_rop_bits": quantum_bits,
                "source": "NTRU family reference security screen",
            }
        },
        "notes": [
            "NTRU reference screens are analytical recommendations, not scheme-level proofs.",
            "Live validation uses the standard lattice-estimator profile when requested.",
        ],
    }
    reference_selected = selected_security_bits(security, request)
    security["ntru_bits"] = (
        reference_selected if math.isfinite(reference_selected) else None
    )
    candidate = {
        "problem": "ntru",
        "ring": {
            "family_id": spec.family_id,
            "family": ntru_family_name(spec.family_id),
            "n": spec.n,
            "cyclotomic_index": 2 * spec.n if spec.family_id == "power2" else None,
            "polynomial": spec.polynomial,
            "quotient": spec.quotient,
            "ntru_type": ntru_type,
            "preset": spec.preset,
        },
        "modulus": {
            "q": spec.q,
            "bits": modulus_bits(spec.q),
            "prime": is_prime(spec.q),
            "q_minus_1_factorization": format_factorization(factor_integer(spec.q - 1)),
            **ntru_modulus_ntt_fields(spec, request),
        },
        "distribution": {
            "family": ntru_distribution_family(spec),
            "name": ntru_distribution_name(spec),
            "fixed_weight": spec.fixed_weight,
            "secret": spec.secret_distribution,
            "error": spec.error_distribution,
            "calibration": spec.calibration,
        },
        "security": security,
        "visual_scores": ntru_visual_scores(spec, request),
        "selection": {
            "target_security": request.target_security,
            "security_model": request.security_model,
            "selected_security_bits": None,
            "margin_bits": None,
            "meets_target": False,
            "status": "target_unmet",
            "security_level": "unclassified",
            "rank_score": None,
        },
        "warnings": [
            "This is an NTRU lattice-hardness prototype. It is not yet bound to scheme-specific "
            "correctness, encoding, failure-rate, or key-invertibility checks.",
        ],
        "warning_codes": ["screen_scheme_not_bound"],
        "notes": [spec.note],
    }
    recalculate_ntru_selection(candidate, request, update_visual=False)
    return candidate


def estimator_ntru_type(request: NTRURequest, spec: NTRUCandidateSpec) -> str:
    if spec.family_id != "power2":
        return "circulant"
    return "matrix" if request.hard_problem_variant == "matrix" else "circulant"


def ntru_ntt_strategy_text(request: NTRURequest) -> str:
    if ntt_scale_is_unrestricted(request.ntt_scale_power):
        return "modulus q third; no n/q divisibility restriction in lift-based NTT mode"
    return "modulus q third; require the selected n/k | q-1 NTT scale and prefer the smallest q"


def ntru_visual_scores(spec: NTRUCandidateSpec, request: NTRURequest) -> dict[str, Any]:
    reference_bits = (
        spec.screen_quantum_bits
        if request.security_model == "quantum"
        else spec.screen_bits
    )
    security_bits = floor_optional_bits(reference_bits)
    compactness = compactness_profile(spec.q, request)
    performance = performance_profile(
        n=spec.n,
        q=spec.q,
        unrestricted=ntt_scale_is_unrestricted(request.ntt_scale_power),
    )
    return {
        "security": {
            "label": "Security",
            "score": (
                round(max(0.0, min(1.0, security_bits / 512.0)), 4)
                if security_bits is not None
                else 0.0
            ),
            "bits": security_bits,
            "max_bits": 512,
        },
        "compactness": compactness,
        "performance": performance,
    }


def select_best_distribution_per_modulus(
    candidates: list[dict[str, Any]],
    request: NTRURequest,
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
        viable = [candidate for candidate in group if candidate["selection"]["meets_target"]]
        pool = viable or group
        selected.append(min(pool, key=lambda c: distribution_rank(c, request)))
    return selected


def distribution_rank(candidate: dict[str, Any], request: NTRURequest) -> tuple[float, ...]:
    recalculate_ntru_selection(candidate, request)
    margin = candidate["selection"]["margin_bits"]
    available = margin is not None
    margin_value = float(margin) if available else 0.0
    stddev = float(candidate["distribution"]["secret"].get("stddev", 0.0))
    shortage = abs(min(0.0, margin_value)) * 10_000.0
    return (
        0.0 if available else 1.0,
        shortage,
        max(0.0, margin_value),
        stddev,
    )


def candidate_rank(candidate: dict[str, Any], request: NTRURequest) -> tuple[float, ...]:
    recalculate_ntru_selection(candidate, request)
    margin = candidate["selection"]["margin_bits"]
    available = margin is not None
    margin_value = float(margin) if available else 0.0
    shortage = abs(min(0.0, margin_value)) * 10_000.0
    family_rank = 0 if candidate["ring"]["family_id"] == request.ring_family else 1
    n = int(candidate["ring"]["n"])
    q = int(candidate["modulus"]["q"])
    stddev = float(candidate["distribution"]["secret"].get("stddev", 0.0))
    rank = (
        0.0 if available else 1.0,
        shortage,
        family_rank,
        n,
        q,
        max(0.0, margin_value),
        stddev,
    )
    candidate["selection"]["rank_score"] = rank
    return rank


def validated_ntru_candidate_rank(
    candidate: dict[str, Any],
    request: NTRURequest,
) -> tuple[Any, ...]:
    recalculate_ntru_selection(candidate, request)
    selected = candidate["selection"]["selected_security_bits"]
    available = selected is not None
    measured_rank = -float(selected) if available else 0.0
    rank = (
        0 if candidate["selection"]["meets_target"] else 1,
        0 if available else 1,
        measured_rank,
        0 if candidate["ring"]["family_id"] == request.ring_family else 1,
        int(candidate["ring"]["n"]),
        int(candidate["modulus"]["bits"]),
        int(candidate["modulus"]["q"]),
        str(candidate["ring"].get("preset") or ""),
    )
    candidate["selection"]["rank_score"] = rank
    return rank


def recalculate_ntru_selection(
    candidate: dict[str, Any],
    request: NTRURequest,
    update_visual: bool = True,
) -> None:
    raw_selected = selected_security_bits(candidate["security"], request)
    selected = raw_selected if math.isfinite(raw_selected) else None
    margin = round(selected - request.target_security, 3) if selected is not None else None
    meets = margin is not None and margin >= 0
    candidate["selection"]["selected_security_bits"] = selected
    candidate["selection"]["margin_bits"] = margin
    candidate["selection"]["meets_target"] = meets
    candidate["selection"]["status"] = selection_status(meets)
    candidate["selection"]["security_level"] = security_level_for_bits(
        selected
    )
    warning_codes = candidate.setdefault("warning_codes", [])
    warnings = candidate.setdefault("warnings", [])
    unavailable = request.security_model == "quantum" and selected is None
    if unavailable:
        candidate["warning_codes"] = list(
            dict.fromkeys(warning_codes + [QUANTUM_ESTIMATE_UNAVAILABLE_CODE])
        )
        candidate["warnings"] = list(
            dict.fromkeys(warnings + [QUANTUM_ESTIMATE_UNAVAILABLE_MESSAGE])
        )
    else:
        candidate["warning_codes"] = [
            code for code in warning_codes if code != QUANTUM_ESTIMATE_UNAVAILABLE_CODE
        ]
        candidate["warnings"] = [
            message
            for message in warnings
            if message != QUANTUM_ESTIMATE_UNAVAILABLE_MESSAGE
        ]
    if update_visual and selected is not None:
        update_visual_security(candidate)


def ntru_validation_candidate_key(candidate: dict[str, Any]) -> str:
    return "|".join(
        (
            str(candidate["ring"]["family_id"]),
            str(candidate["ring"].get("preset") or ""),
            str(candidate["ring"]["n"]),
            str(candidate["modulus"]["q"]),
            str(candidate["distribution"]["secret"].get("name", "")),
            str(candidate["distribution"]["error"].get("name", "")),
        )
    )


def sanitize_json_metadata(
    value: Any,
    seen: set[int] | None = None,
    depth: int = 0,
) -> Any:
    if depth > 32:
        return "<maximum-depth-exceeded>"
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value if value.bit_length() <= 4096 else "<integer-too-large>"
    if isinstance(value, float):
        return value if math.isfinite(value) else None

    seen = seen if seen is not None else set()
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            return "<recursive-reference>"
        seen.add(identity)
        try:
            return {
                sanitize_json_key(key): sanitize_json_metadata(item, seen, depth + 1)
                for key, item in value.items()
            }
        finally:
            seen.remove(identity)
    if isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in seen:
            return "<recursive-reference>"
        seen.add(identity)
        try:
            items = list(value)
            if isinstance(value, (set, frozenset)):
                items.sort(key=lambda item: safe_diagnostic_string(item))
            return [
                sanitize_json_metadata(item, seen, depth + 1)
                for item in items
            ]
        finally:
            seen.remove(identity)
    return safe_diagnostic_string(value)


def sanitize_json_key(key: Any) -> str:
    if isinstance(key, str):
        return key
    if isinstance(key, bool):
        return "true" if key else "false"
    if isinstance(key, int):
        return str(key) if key.bit_length() <= 4096 else "<integer-key-too-large>"
    if isinstance(key, float) and not math.isfinite(key):
        return "<nonfinite-key>"
    return safe_diagnostic_string(key)


def safe_diagnostic_string(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__}>"


def effective_ntru_security_variant(
    candidate: dict[str, Any],
    request: NTRURequest | None,
) -> str:
    if candidate["ring"]["family_id"] == "power2" and request is not None:
        return request.hard_problem_variant
    return "matrix" if candidate["ring"]["ntru_type"] == "matrix" else "ring"


def ntru_estimator_provenance(
    candidate: dict[str, Any],
    request: NTRURequest | None,
) -> dict[str, Any]:
    effective_variant = effective_ntru_security_variant(candidate, request)
    requested_variant = (
        request.hard_problem_variant if request is not None else effective_variant
    )
    return {
        "hard_problem_variant": effective_variant,
        "requested_hard_problem_variant": requested_variant,
        "ring_degree": int(candidate["ring"]["n"]),
    }


def normalize_ntru_estimator_response(
    response: Any,
    candidate: dict[str, Any],
    request: NTRURequest,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    safe_response = sanitize_json_metadata(response)
    normalized, validation_entry = normalize_estimator_response(
        safe_response,
        request=request,
        expected_profile=NTRU_ESTIMATOR_PROFILE,
    )
    provenance = ntru_estimator_provenance(candidate, request)
    validation_entry = with_ntru_estimator_provenance(validation_entry, provenance)
    if normalized is None:
        return None, validation_entry
    return extract_ntru_estimator_result(normalized, provenance), validation_entry


def with_ntru_estimator_provenance(
    entry: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(entry)
    enriched.update(provenance)
    parameters = enriched.get("parameters")
    if isinstance(parameters, dict):
        enriched["parameters"] = dict(parameters) | provenance
    return enriched


def extract_ntru_estimator_result(
    result: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    models = result.get("models", {})
    modes = result.get("modes", {})
    extracted_models = {}
    for model in NTRU_ESTIMATOR_MODELS:
        model_modes = models.get(model)
        if not isinstance(model_modes, dict):
            continue
        extracted_models[model] = {
            mode: extract_ntru_mode_result(model_modes[mode])
            for mode in NTRU_ESTIMATOR_MODES
            if mode in model_modes
        }
    extracted_modes = {
        mode: extract_ntru_mode_result(modes[mode])
        for mode in NTRU_ESTIMATOR_MODES
        if mode in modes
    }
    return {
        "ok": True,
        "complete": result["complete"],
        "estimator_profile": NTRU_ESTIMATOR_PROFILE,
        "estimator_commit": result.get("estimator_commit"),
        "models": extracted_models,
        "modes": extracted_modes,
        **provenance,
    }


def extract_ntru_mode_result(mode_result: dict[str, Any]) -> dict[str, Any]:
    extracted = {
        "ok": mode_result.get("ok") is True,
        "complete": mode_result.get("complete") is True,
        "attacks": sanitize_json_metadata(mode_result.get("attacks", {})),
    }
    bits = parse_security_bits(mode_result.get("min_bits"))
    if bits is not None:
        extracted["min_bits"] = bits
    for field in ("best_attack", "code", "message"):
        value = mode_result.get(field)
        if isinstance(value, str):
            extracted[field] = value
    return extracted


def run_ntru_estimator(
    candidate: dict[str, Any],
    timeout: int,
    config: AppConfig | None = None,
    request: NTRURequest | None = None,
) -> dict[str, Any]:
    config = config or load_config()
    provenance = ntru_estimator_provenance(candidate, request)
    payload = {
        "problem": "ntru",
        "n": candidate["ring"]["n"],
        "q": candidate["modulus"]["q"],
        "ntru_type": (
            "matrix"
            if provenance["hard_problem_variant"] == "matrix"
            else "circulant"
        ),
        "secret_distribution": candidate["distribution"]["secret"],
        "error_distribution": candidate["distribution"]["error"],
        **provenance,
        "per_attack_timeout": max(
            5,
            min(90, config.estimator.per_attack_timeout_seconds * 2),
        ),
    }
    return run_estimator(payload, timeout, config, NTRU_ESTIMATOR_PROFILE)


def apply_ntru_estimator_result(
    candidate: dict[str, Any],
    estimator_result: dict[str, Any],
    request: NTRURequest,
) -> None:
    model_bits = {
        model: {
            mode: ntru_estimator_model_bits(estimator_result, model, mode)
            for mode in NTRU_ESTIMATOR_MODES
        }
        for model in NTRU_ESTIMATOR_MODELS
    }
    capped = {
        model: {
            mode: (
                cap_composite_estimate(candidate, floor_bits(value))
                if value is not None
                else None
            )
            for mode, value in modes.items()
        }
        for model, modes in model_bits.items()
    }
    adps16 = capped["adps16"]
    matzov = capped["matzov"]
    notes = [
        "Estimated with lattice-estimator NTRU attacks under MATZOV and ADPS16 cost models.",
        "Available model and mode estimates are reported without reference-screen substitution.",
    ]
    was_capped = any(
        capped[model][mode] is not None
        and model_bits[model][mode] is not None
        and capped[model][mode] < model_bits[model][mode]
        for model in capped
        for mode in capped[model]
    )
    if was_capped:
        notes.append(
            "Composite fast distributions are estimator moment approximations; the reported bit count is capped "
            "by the discrete-Gaussian proxy calibration to avoid overstating security."
        )
    previous_security = candidate["security"]
    classical_bits = (
        adps16["classical"]
        if adps16["classical"] is not None
        else matzov["classical"]
    )
    quantum_bits = (
        adps16["quantum"]
        if adps16["quantum"] is not None
        else matzov["quantum"]
    )
    candidate["security"] = {
        "source": "sage-lattice-estimator-ntru",
        "source_code": "sage_standard",
        "classical_bits": classical_bits,
        "quantum_bits": quantum_bits,
        "matzov_bits": matzov["classical"],
        "matzov_quantum_bits": matzov["quantum"],
        "adps16_core_svp_bits": adps16["classical"],
        "adps16_quantum_bits": adps16["quantum"],
        "ntru_bits": None,
        "reference_classical_bits": previous_security.get("reference_classical_bits"),
        "reference_quantum_bits": previous_security.get("reference_quantum_bits"),
        "reference_attack": previous_security.get("reference_attack"),
        "nist_category": previous_security.get("nist_category"),
        "reference_screen": previous_security.get("reference_screen"),
        "attacks": estimator_result.get("models") or estimator_result.get("modes", {}),
        "estimator_commit": estimator_result.get("estimator_commit"),
        "hard_problem_variant": estimator_result.get("hard_problem_variant"),
        "requested_hard_problem_variant": estimator_result.get(
            "requested_hard_problem_variant"
        ),
        "ring_degree": estimator_result.get("ring_degree"),
        "notes": notes,
    }
    selected = selected_security_bits(candidate["security"], request)
    candidate["security"]["ntru_bits"] = selected if math.isfinite(selected) else None
    recalculate_ntru_selection(candidate, request)
    candidate["warnings"].append(
        "Sage/lattice-estimator NTRU validation was applied to this recommendation."
    )
    candidate["warning_codes"] = list(
        dict.fromkeys(candidate.get("warning_codes", []) + ["validation_applied"])
    )
    if was_capped:
        candidate["warnings"].append(
            "Composite fast distributions are currently passed to the estimator through a same-variance "
            "moment approximation; the reported bit count is capped by the Gaussian proxy calibration."
        )


def ntru_estimator_model_bits(estimator_result: dict[str, Any], model: str, mode: str) -> float | None:
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


def cap_composite_estimate(candidate: dict[str, Any], raw_bits: float) -> float:
    estimator = candidate["distribution"]["secret"].get("estimator", {})
    if estimator.get("type") != "composite_moment":
        return raw_bits
    calibration = candidate["distribution"].get("calibration") or {}
    proxy_bits = calibration.get("gaussian_proxy_bits")
    if proxy_bits is None:
        return raw_bits
    return floor_bits(min(float(raw_bits), float(proxy_bits)))


def ntru_family_name(family_id: str) -> str:
    if family_id == "power2":
        return "2-power cyclotomic NTRU"
    if family_id == "hps":
        return "NTRU-HPS style"
    if family_id == "hrss":
        return "NTRU-HRSS style"
    if family_id == "ntru_prime":
        return "Streamlined NTRU Prime"
    return family_id


def ntru_modulus_ntt_fields(
    spec: NTRUCandidateSpec,
    request: NTRURequest,
) -> dict[str, Any]:
    nullable_fields = {
        "ntt_condition": None,
        "ntt_friendly": None,
        "ntt_quality": None,
        "ntt_layers_remaining": None,
        "polynomial_factorization": None,
        "factor_count": None,
        "factor_degree": None,
        "decomposition_score": None,
        "two_adicity": None,
        "small_factor_weight": None,
    }
    if spec.family_id != "power2":
        return nullable_fields

    factors = factor_integer(spec.q - 1)
    unrestricted = ntt_scale_is_unrestricted(request.ntt_scale_power)
    return {
        **nullable_fields,
        "ntt_condition": ntru_ntt_condition(spec),
        "ntt_friendly": not unrestricted,
        "ntt_quality": "lift_ntt_unfriendly" if unrestricted else "selected_scale",
        "two_adicity": factors.get(2, 0),
        "small_factor_weight": sum(
            exponent
            for prime, exponent in factors.items()
            if prime <= 31
        ),
    }


def ntru_ntt_condition(spec: NTRUCandidateSpec) -> str | None:
    if spec.family_id != "power2":
        return None
    full = 2 * spec.n
    half = max(1, spec.n // 2)
    if (spec.q - 1) % full == 0:
        return f"{full} | q - 1; full split for x^{spec.n}+1"
    if (spec.q - 1) % spec.n == 0:
        return f"{spec.n} | q - 1; one layer below full split"
    if (spec.q - 1) % half == 0:
        return f"{half} | q - 1; same relaxed NTT scale as RLWE n/2"
    return f"partial NTT: {(spec.q - 1) & -(spec.q - 1)} divides q - 1"


def ntru_satisfies_ntt_requirement(spec: NTRUCandidateSpec, request: NTRURequest) -> bool:
    if spec.family_id != "power2":
        return True
    if ntt_scale_is_unrestricted(request.ntt_scale_power):
        return True
    divisor = ntru_required_ntt_divisor(spec.n, request.ntt_scale_power)
    return (spec.q - 1) % divisor == 0


def ntru_required_ntt_divisor(n: int, ntt_scale_power: int) -> int:
    if ntt_scale_power < 0:
        return n * (2 ** abs(ntt_scale_power))
    return max(1, n // (2**ntt_scale_power))


def ntru_distribution_family(spec: NTRUCandidateSpec) -> str:
    if spec.secret_distribution["family"] == spec.error_distribution["family"]:
        return spec.secret_distribution["family"]
    return f"{spec.secret_distribution['family']} / {spec.error_distribution['family']}"


def ntru_distribution_name(spec: NTRUCandidateSpec) -> str:
    if spec.secret_distribution["name"] == spec.error_distribution["name"]:
        return spec.secret_distribution["name"]
    return f"Xs={spec.secret_distribution['name']}, Xe={spec.error_distribution['name']}"


def gaussian_distribution(stddev: float) -> dict[str, Any]:
    return {
        "family": "discrete_gaussian",
        "name": f"DGaussian(sigma={stddev})",
        "mean": 0.0,
        "variance": round(stddev * stddev, 9),
        "stddev": stddev,
        "support": ["Z"],
        "symmetric": True,
        "sampling": "discrete Gaussian sampler required; prototype does not certify sampler quality",
        "estimator": {"type": "discrete_gaussian", "stddev": stddev},
    }


def closest_fast_distribution(n: int, sigma_lower_bound: float) -> dict[str, Any]:
    candidates = [
        distribution
        for distribution in fast_distribution_candidates(n)
        if float(distribution["stddev"]) >= sigma_lower_bound
    ]
    if not candidates:
        fallback = gaussian_distribution(sigma_lower_bound)
        fallback["sampling"] = "fallback only; no fast distribution met the Gaussian lower bound"
        return fallback
    return min(
        candidates,
        key=lambda distribution: (
            float(distribution["stddev"]) - sigma_lower_bound,
            len(distribution.get("components", [distribution])),
            fast_distribution_family_rank(distribution["family"]),
            distribution["name"],
        ),
    )


def fast_distribution_candidates(n: int) -> list[dict[str, Any]]:
    base: list[dict[str, Any]] = []
    for l0, l1 in (
        (4, 2),
        (4, 1),
        (3, 2),
        (4, 0),
        (3, 1),
        (2, 1),
        (3, 0),
        (2, 0),
        (1, 0),
    ):
        base.append(sparse_ternary_probability_distribution(n=n, l0=l0, l1=l1))
    for radius in (1, 2, 3):
        base.append(symmetric_uniform_distribution(radius))
    for eta in range(1, 9):
        base.append(centered_binomial_distribution(eta))

    candidates = list(base)
    for size in (2, 3):
        for components in combinations_with_replacement(base, size):
            candidates.append(composite_distribution(components))
    return candidates


def fast_distribution_family_rank(family: str) -> int:
    order = {
        "sparse_ternary_fixed_weight": 0,
        "composite": 1,
        "symmetric_uniform": 2,
        "centered_binomial": 2,
        "uniform_mod": 3,
    }
    return order.get(family, 10)


def composite_distribution(components: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    variance = sum(float(component["variance"]) for component in components)
    stddev = math.sqrt(variance)
    support_low = sum(numeric_support(component)[0] for component in components)
    support_high = sum(numeric_support(component)[1] for component in components)
    names = [component["name"] for component in components]
    return {
        "family": "composite",
        "name": " + ".join(names),
        "mean": 0.0,
        "variance": round(variance, 9),
        "stddev": round(stddev, 9),
        "support": [support_low, support_high],
        "symmetric": all(bool(component.get("symmetric")) for component in components),
        "sampling": "sample each listed fast component independently and add the coefficients",
        "components": [component_summary(component) for component in components],
        "estimator": {
            "type": "composite_moment",
            "stddev": stddev,
            "bounds": [support_low, support_high],
            "note": "moment approximation for a sum of fast-sampling centered distributions",
        },
    }


def component_summary(component: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": component["family"],
        "name": component["name"],
        "variance": component["variance"],
        "stddev": component["stddev"],
        "support": component["support"],
        "estimator": component["estimator"],
    }


def numeric_support(distribution: dict[str, Any]) -> tuple[int, int]:
    support = distribution.get("support", [0, 0])
    if len(support) != 2 or not all(isinstance(value, int) for value in support):
        return (-10, 10)
    return int(support[0]), int(support[1])


def centered_binomial_distribution(eta: int) -> dict[str, Any]:
    variance = eta / 2
    return {
        "family": "centered_binomial",
        "name": f"CBD({eta})",
        "mean": 0.0,
        "variance": round(variance, 9),
        "stddev": round(math.sqrt(variance), 9),
        "support": [-eta, eta],
        "symmetric": True,
        "sampling": "bit-sliced popcount friendly",
        "estimator": {"type": "centered_binomial", "eta": eta},
    }


def symmetric_uniform_distribution(radius: int) -> dict[str, Any]:
    variance = radius * (radius + 1) / 3
    return {
        "family": "symmetric_uniform",
        "name": f"SymUniform({radius})",
        "mean": 0.0,
        "variance": round(variance, 9),
        "stddev": round(math.sqrt(variance), 9),
        "support": [-radius, radius],
        "symmetric": True,
        "sampling": f"uniform centered coefficients in [-{radius}, {radius}]",
        "estimator": {"type": "uniform_mod", "modulus": 2 * radius + 1},
    }


def uniform_mod_distribution(modulus: int) -> dict[str, Any]:
    stddev = math.sqrt((modulus * modulus - 1) / 12)
    return {
        "family": "uniform_mod",
        "name": f"UniformMod({modulus})",
        "mean": 0.0,
        "variance": round(stddev * stddev, 9),
        "stddev": round(stddev, 9),
        "support": [-(modulus // 2), modulus // 2],
        "symmetric": True,
        "sampling": "uniform centered coefficients modulo small integer",
        "estimator": {"type": "uniform_mod", "modulus": modulus},
    }


def sparse_ternary_distribution(p: int, m: int, n: int) -> dict[str, Any]:
    variance = (p + m) / n
    return {
        "family": "sparse_ternary_fixed_weight",
        "name": f"SparseTernary(p={p}, m={m})",
        "mean": round((p - m) / n, 9),
        "variance": round(variance, 9),
        "stddev": round(math.sqrt(variance), 9),
        "support": [-1, 1],
        "symmetric": p == m,
        "sampling": "fixed-weight sparse ternary sampler",
        "estimator": {
            "type": "sparse_ternary_fixed_weight",
            "plus_weight": p,
            "minus_weight": m,
        },
    }


def sparse_ternary_probability_distribution(n: int, l0: int, l1: int) -> dict[str, Any]:
    probability_each = ((2**l0) - 1) / (2 ** (2 * l0 + l1))
    p = max(0, round(n * probability_each))
    distribution = sparse_ternary_distribution(p, p, n)
    distribution["name"] = f"ST(l0={l0}, l1={l1})"
    distribution["parameters"] = {
        "l0": l0,
        "l1": l1,
        "probability_plus": probability_each,
        "probability_minus": probability_each,
        "probability_zero": 1 - 2 * probability_each,
        "plus_weight": p,
        "minus_weight": p,
    }
    distribution["sampling"] = "sample sign/magnitude from bit arithmetic; zero otherwise"
    distribution["estimator"]["note"] = "fixed-weight approximation to iid sparse ternary"
    return distribution
