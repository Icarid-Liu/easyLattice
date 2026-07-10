from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from itertools import combinations_with_replacement
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .parameter_search import (
    NTT_UNFRIENDLY_SCALE_POWER,
    SUPPORTED_RED_COST_MODELS,
    SUPPORTED_SECURITY_MODELS,
    compactness_profile,
    factor_integer,
    floor_bits,
    format_factorization,
    is_prime,
    ntt_scale_is_unrestricted,
    parse_hard_problem,
    performance_profile,
    meets_target,
    security_margin_bits,
    selected_security_bits,
    update_visual_security,
    security_level_for_bits,
)
from .remote_estimator import estimate_remotely


SUPPORTED_NTRU_RING_FAMILIES = {"auto", "power2", "hps", "hrss"}


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
    calibration: dict[str, Any] | None = None


def recommend_ntru(raw: dict[str, Any] | None = None, config: AppConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    request = parse_ntru_request(raw or {}, config=config)
    started = time.perf_counter()
    specs = ntru_candidate_specs(request)
    if not specs:
        raise ValueError("No NTRU candidates could be generated for the requested bounds.")

    candidates = [make_ntru_candidate(spec, request) for spec in specs]
    candidates = select_best_distribution_per_modulus(candidates, request)

    estimator_result = None
    if request.use_estimator:
        estimator_result = {"ok": True, "validated": []}
        validated = []
        for candidate in sorted(candidates, key=lambda c: candidate_rank(c, request))[: request.validation_attempts]:
            result = run_ntru_estimator(candidate, request.estimator_timeout, config=config)
            estimator_result["validated"].append(result)
            if result.get("ok"):
                apply_ntru_estimator_result(candidate, result, request)
                validated.append(candidate)
            else:
                estimator_result["ok"] = False
                candidate["warnings"].append(result.get("message", "NTRU estimator failed for this candidate."))
        if validated:
            candidates = validated
        elif request.security_model == "quantum":
            raise ValueError("NTRU quantum Sage evaluation did not complete for any candidate.")

    viable = [candidate for candidate in candidates if candidate["selection"]["meets_target"]]
    ranked = sorted(viable or candidates, key=lambda c: candidate_rank(c, request))
    recommendation = ranked[0]
    alternatives = ranked[1:5]
    elapsed_ms = round((time.perf_counter() - started) * 1000)

    return {
        "request": asdict(request) | {"problem": "ntru"},
        "recommendation": recommendation,
        "alternatives": alternatives,
        "estimator": estimator_result,
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
        raise ValueError("NTRU ring_family must be one of auto, power2, hps, hrss.")
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
    if security_model == "quantum" and not use_estimator:
        raise ValueError("NTRU quantum targets require useEstimator=true for Sage evaluation.")

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
        validation_attempts=max(validation_count, min(24, validation_attempts)),
    )


def ntru_candidate_specs(request: NTRURequest) -> list[NTRUCandidateSpec]:
    specs = []
    families = ("power2", "hps", "hrss") if request.ring_family == "auto" else (request.ring_family,)
    for family in families:
        if family == "power2":
            specs.extend(power2_specs())
        elif family == "hps":
            specs.extend(hps_specs())
        elif family == "hrss":
            specs.extend(hrss_specs())
    return [
        spec
        for spec in specs
        if request.min_n <= spec.n <= request.max_n
        and request.min_q_bits <= spec.q.bit_length() <= request.max_q_bits
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


def hps_spec(N: int, bits: float) -> NTRUCandidateSpec:
    n = N - 1
    q = 2048
    return NTRUCandidateSpec(
        family_id="hps",
        n=n,
        q=q,
        polynomial=f"x^{N} - 1 with one relation removed by the estimator",
        quotient=f"NTRU-HPS style mod q={q}, public polynomial degree N={N}",
        ntru_type="matrix",
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
        ntru_type="matrix",
        secret_distribution=uniform_mod_distribution(3),
        error_distribution=uniform_mod_distribution(3),
        screen_bits=bits,
        screen_attack="usvp",
        note="HRSS-like NTRU candidate",
    )


def make_ntru_candidate(spec: NTRUCandidateSpec, request: NTRURequest) -> dict[str, Any]:
    ntru_type = estimator_ntru_type(request)
    security = {
        "source": "ntru-reference-screen",
        "classical_bits": floor_bits(spec.screen_bits),
        "quantum_bits": None,
        "ntru_bits": floor_bits(spec.screen_bits),
        "attacks": {
            spec.screen_attack: {
                "ok": True,
                "rop_bits": floor_bits(spec.screen_bits),
                "source": "precomputed local lattice-estimator rough screen",
            }
        },
        "notes": [
            "NTRU screen uses lattice-estimator rough NTRU attacks where available.",
            "Quantum NTRU bit estimates are not reported in this prototype.",
        ],
    }
    candidate = {
        "problem": "ntru",
        "ring": {
            "family_id": spec.family_id,
            "family": ntru_family_name(spec.family_id),
            "n": spec.n,
            "polynomial": spec.polynomial,
            "quotient": spec.quotient,
            "ntru_type": ntru_type,
        },
        "modulus": {
            "q": spec.q,
            "bits": spec.q.bit_length(),
            "prime": is_prime(spec.q),
            "q_minus_1_factorization": format_factorization(factor_integer(spec.q - 1)),
            "ntt_condition": ntru_ntt_condition(spec),
        },
        "distribution": {
            "family": ntru_distribution_family(spec),
            "name": ntru_distribution_name(spec),
            "secret": spec.secret_distribution,
            "error": spec.error_distribution,
            "calibration": spec.calibration,
        },
        "security": security,
        "visual_scores": ntru_visual_scores(spec, request),
        "selection": {
            "target_security": request.target_security,
            "security_model": request.security_model,
            "selected_security_bits": floor_bits(spec.screen_bits),
            "margin_bits": floor_bits(spec.screen_bits - request.target_security),
            "meets_target": spec.screen_bits >= request.target_security,
            "security_level": security_level_for_bits(floor_bits(spec.screen_bits)),
            "rank_score": None,
        },
        "warnings": [
            "This is an NTRU lattice-hardness prototype. It is not yet bound to scheme-specific "
            "correctness, encoding, failure-rate, or key-invertibility checks.",
        ],
        "notes": [spec.note],
    }
    return candidate


def estimator_ntru_type(request: NTRURequest) -> str:
    if request.hard_problem_variant == "matrix":
        return "matrix"
    return "circulant"


def ntru_ntt_strategy_text(request: NTRURequest) -> str:
    if ntt_scale_is_unrestricted(request.ntt_scale_power):
        return "modulus q third; no n/q divisibility restriction in lift-based NTT mode"
    return "modulus q third; require the selected n/k | q-1 NTT scale and prefer the smallest q"


def ntru_visual_scores(spec: NTRUCandidateSpec, request: NTRURequest) -> dict[str, Any]:
    security_bits = floor_bits(spec.screen_bits)
    compactness = compactness_profile(spec.q, request)
    performance = performance_profile(
        n=spec.n,
        q=spec.q,
        unrestricted=ntt_scale_is_unrestricted(request.ntt_scale_power),
    )
    return {
        "security": {
            "label": "Security",
            "score": round(max(0.0, min(1.0, security_bits / 512.0)), 4),
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
    margin = float(candidate["selection"]["margin_bits"])
    stddev = float(candidate["distribution"]["secret"].get("stddev", 0.0))
    shortage = abs(min(0.0, margin)) * 10_000.0
    return (shortage, max(0.0, margin), stddev)


def candidate_rank(candidate: dict[str, Any], request: NTRURequest) -> tuple[float, ...]:
    margin = float(candidate["selection"]["margin_bits"])
    shortage = abs(min(0.0, margin)) * 10_000.0
    family_rank = 0 if candidate["ring"]["family_id"] == request.ring_family else 1
    n = int(candidate["ring"]["n"])
    q = int(candidate["modulus"]["q"])
    stddev = float(candidate["distribution"]["secret"].get("stddev", 0.0))
    rank = (shortage, family_rank, n, q, max(0.0, margin), stddev)
    candidate["selection"]["rank_score"] = rank
    return rank


def run_ntru_estimator(
    candidate: dict[str, Any],
    timeout: int,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    config = config or load_config()
    payload = {
        "problem": "ntru",
        "n": candidate["ring"]["n"],
        "q": candidate["modulus"]["q"],
        "ntru_type": candidate["ring"]["ntru_type"],
        "secret_distribution": candidate["distribution"]["secret"],
        "error_distribution": candidate["distribution"]["error"],
        "per_attack_timeout": max(5, min(90, config.estimator.per_attack_timeout_seconds * 2)),
    }
    if config.estimator.remote_url:
        result = estimate_remotely(
            base_url=config.estimator.remote_url,
            payload=payload,
            timeout_seconds=config.estimator.remote_timeout_seconds,
            poll_interval_seconds=config.estimator.remote_poll_interval_seconds,
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "message": result.get("message", "Remote Sage/lattice-estimator could not estimate this NTRU candidate."),
                "raw": result,
            }
        return result

    sage_binary = config.estimator.sage_binary
    sage = shutil.which(sage_binary) or (sage_binary if Path(sage_binary).exists() else None)
    if not sage:
        return {
            "ok": False,
            "message": f"Sage binary '{sage_binary}' not found; using NTRU screen estimate only.",
        }

    runner = Path(__file__).with_name("estimator_runner.py")
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
            "message": f"Sage/lattice-estimator timed out after {timeout}s; keeping NTRU screen estimate.",
        }

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        suffix = f" Detail: {detail[0]}" if detail else ""
        return {
            "ok": False,
            "message": f"Sage/lattice-estimator failed with exit code {completed.returncode}.{suffix}",
        }

    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {
            "ok": False,
            "message": "Sage/lattice-estimator returned non-JSON output; keeping NTRU screen estimate.",
        }


def apply_ntru_estimator_result(
    candidate: dict[str, Any],
    estimator_result: dict[str, Any],
    request: NTRURequest,
) -> None:
    model_bits = {
        model: {
            mode: ntru_estimator_model_bits(estimator_result, model, mode)
            for mode in ("classical", "quantum")
        }
        for model in ("matzov", "adps16")
    }
    if any(value is None for modes in model_bits.values() for value in modes.values()):
        raise ValueError("NTRU estimator did not produce all MATZOV/ADPS16 classical and quantum estimates.")
    capped = {
        model: {
            mode: cap_composite_estimate(candidate, floor_bits(float(value)))
            for mode, value in modes.items()
        }
        for model, modes in model_bits.items()
    }
    adps16 = capped["adps16"]
    matzov = capped["matzov"]
    notes = [
        "Estimated with lattice-estimator NTRU attacks under MATZOV and ADPS16 cost models.",
        "Classical and quantum values use the same NTRU parameters and attack set.",
    ]
    if any(capped[model][mode] < model_bits[model][mode] for model in capped for mode in capped[model]):
        notes.append(
            "Composite fast distributions are estimator moment approximations; the reported bit count is capped "
            "by the discrete-Gaussian proxy calibration to avoid overstating security."
        )
    candidate["security"] = {
        "source": "sage-lattice-estimator-ntru",
        "classical_bits": adps16["classical"],
        "quantum_bits": adps16["quantum"],
        "matzov_bits": matzov["classical"],
        "matzov_quantum_bits": matzov["quantum"],
        "adps16_core_svp_bits": adps16["classical"],
        "adps16_quantum_bits": adps16["quantum"],
        "ntru_bits": selected_security_bits(
            {
                "classical_bits": adps16["classical"],
                "quantum_bits": adps16["quantum"],
                "matzov_bits": matzov["classical"],
                "matzov_quantum_bits": matzov["quantum"],
                "adps16_core_svp_bits": adps16["classical"],
                "adps16_quantum_bits": adps16["quantum"],
            },
            request,
        ),
        "attacks": estimator_result.get("models", estimator_result["modes"]),
        "estimator_commit": estimator_result.get("estimator_commit"),
        "notes": notes,
    }
    candidate["selection"]["selected_security_bits"] = selected_security_bits(candidate["security"], request)
    candidate["selection"]["margin_bits"] = security_margin_bits(candidate["security"], request)
    candidate["selection"]["meets_target"] = meets_target(candidate["security"], request)
    candidate["selection"]["security_level"] = security_level_for_bits(candidate["selection"]["selected_security_bits"])
    update_visual_security(candidate)
    candidate["warnings"].append("Sage/lattice-estimator NTRU validation was applied with four cost models.")
    if any(capped[model][mode] < model_bits[model][mode] for model in capped for mode in capped[model]):
        candidate["warnings"].append(
            "Composite fast distributions are currently passed to the estimator through a same-variance "
            "moment approximation; the reported bit count is capped by the Gaussian proxy calibration."
        )


def ntru_estimator_model_bits(estimator_result: dict[str, Any], model: str, mode: str) -> float | None:
    model_modes = estimator_result.get("models", {}).get(model)
    mode_result = model_modes.get(mode, {}) if isinstance(model_modes, dict) else estimator_result.get("modes", {}).get(mode, {})
    if mode_result.get("ok") and mode_result.get("min_bits") is not None:
        return float(mode_result["min_bits"])
    return None


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
    return family_id


def ntru_ntt_condition(spec: NTRUCandidateSpec) -> str:
    if spec.family_id != "power2":
        return "not NTT-prime selected; q is an NTRU power-of-two style modulus"
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
