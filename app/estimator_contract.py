from __future__ import annotations

from typing import Any


LWE_ATTACKS = ("usvp", "dual_hybrid", "bdd_hybrid")
STRUCTURED_LWE_VARIANTS = frozenset({"rlwe", "mlwe", "rlwr", "mlwr"})

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
