from __future__ import annotations

from typing import Any


LWE_ATTACKS = ("usvp", "dual_hybrid", "bdd_hybrid")
STANDARD_LWE_VARIANTS = frozenset({"lwe", "lwr"})
STRUCTURED_LWE_VARIANTS = frozenset({"rlwe", "mlwe", "rlwr", "mlwr"})
NTRU_TYPE_BY_VARIANT = {
    "matrix": "matrix",
    "ring": "circulant",
    "hps": "circulant",
    "hrss": "circulant",
    "ntru_prime": "circulant",
}
NTRU_VARIANTS = frozenset(NTRU_TYPE_BY_VARIANT)
ESTIMATOR_PROFILES = frozenset({"standard", "enhanced"})


class EstimatorRouteError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_result(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": self.message}


def validate_estimator_route(
    problem: Any,
    estimator_profile: Any,
    hard_problem_variant: Any,
    ntru_type: Any = None,
) -> tuple[str, str, str]:
    if not isinstance(problem, str) or problem not in {"lwe", "ntru"}:
        raise EstimatorRouteError(
            "invalid_estimator_problem",
            "problem must be lwe or ntru.",
        )
    if (
        not isinstance(estimator_profile, str)
        or estimator_profile not in ESTIMATOR_PROFILES
    ):
        raise EstimatorRouteError(
            "invalid_estimator_profile",
            "estimator_profile must be standard or enhanced.",
        )
    if not isinstance(hard_problem_variant, str):
        raise EstimatorRouteError(
            "invalid_estimator_route",
            "hard_problem_variant is required and must match the selected estimator profile.",
        )

    if problem == "lwe" and estimator_profile == "standard":
        allowed = STANDARD_LWE_VARIANTS
    elif problem == "lwe" and estimator_profile == "enhanced":
        allowed = STRUCTURED_LWE_VARIANTS
    elif problem == "ntru" and estimator_profile == "standard":
        allowed = NTRU_VARIANTS
    else:
        allowed = frozenset()
    if hard_problem_variant not in allowed:
        allowed_text = ", ".join(sorted(allowed)) if allowed else "none"
        raise EstimatorRouteError(
            "invalid_estimator_route",
            f"{problem}/{estimator_profile} requires hard_problem_variant in {allowed_text}.",
        )
    if problem == "ntru":
        validate_ntru_type(hard_problem_variant, ntru_type)
    return problem, estimator_profile, hard_problem_variant


def ntru_type_for_variant(hard_problem_variant: Any) -> str:
    if not isinstance(hard_problem_variant, str):
        raise EstimatorRouteError(
            "invalid_estimator_route",
            "NTRU hard_problem_variant is required.",
        )
    try:
        return NTRU_TYPE_BY_VARIANT[hard_problem_variant]
    except KeyError as exc:
        allowed = ", ".join(sorted(NTRU_TYPE_BY_VARIANT))
        raise EstimatorRouteError(
            "invalid_estimator_route",
            f"NTRU hard_problem_variant must be one of {allowed}.",
        ) from exc


def validate_ntru_type(hard_problem_variant: Any, ntru_type: Any) -> str:
    expected = ntru_type_for_variant(hard_problem_variant)
    if not isinstance(ntru_type, str):
        raise EstimatorRouteError(
            "invalid_estimator_route",
            "ntru_type is required for NTRU estimator payloads and responses.",
        )
    if ntru_type != expected:
        raise EstimatorRouteError(
            "invalid_estimator_route",
            f"hard_problem_variant={hard_problem_variant} requires ntru_type={expected}.",
        )
    return expected

STRUCTURE_NOT_APPLICABLE = {
    "requested": False,
    "available": False,
    "applied": False,
    "code": "structure_correction_not_applicable",
    "message": "No explicit ring-structure correction is requested for this attack.",
}
STRUCTURE_DUAL_UNAVAILABLE = {
    "requested": True,
    "available": False,
    "applied": False,
    "code": "structure_correction_unavailable",
    "message": (
        "The pinned enhanced estimator has no explicit ring-structure correction "
        "for dual_hybrid; its finite estimate is reported for inspection only."
    ),
}
STRUCTURE_BDD_APPLIED = {
    "requested": True,
    "available": True,
    "applied": True,
    "code": "structure_correction_applied",
    "message": (
        "The pinned enhanced estimator applies deg_ring and structure_leverage "
        "to bdd_hybrid."
    ),
}


def structure_correction_metadata(
    attack: str,
    estimator_profile: str,
    hard_problem_variant: str,
) -> dict[str, Any]:
    structured = hard_problem_variant in STRUCTURED_LWE_VARIANTS
    if not structured or estimator_profile != "enhanced" or attack == "usvp":
        return dict(STRUCTURE_NOT_APPLICABLE)
    if attack == "dual_hybrid":
        return dict(STRUCTURE_DUAL_UNAVAILABLE)
    if attack == "bdd_hybrid":
        return dict(STRUCTURE_BDD_APPLIED)
    raise ValueError(f"Unsupported LWE attack: {attack}")


def structure_correction_satisfied(attack_result: dict[str, Any]) -> bool:
    correction = attack_result.get("structure_correction")
    if not isinstance(correction, dict):
        return True
    return correction.get("requested") is not True or correction.get("applied") is True
