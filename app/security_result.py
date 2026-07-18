from __future__ import annotations

from typing import Any


def modulus_bits(q: int) -> int:
    if q < 2:
        raise ValueError("q must be at least 2.")
    return (q - 1).bit_length()


def selection_status(meets: bool) -> str:
    return "target_met" if meets else "target_unmet"


def validation_result(
    requested: bool,
    profile: str,
    attempted: int,
    successful: int,
    covered: int,
    eligible: int,
    attacks_complete: bool,
    estimator_commit: str | None = None,
    message_codes: list[str] | None = None,
) -> dict[str, Any]:
    if not requested:
        status = "not_requested"
    elif successful == 0:
        status = "failed"
    elif covered == eligible and successful == attempted == eligible and attacks_complete:
        status = "validated"
    else:
        status = "partial"

    return {
        "requested": requested,
        "status": status,
        "profile": profile,
        "estimator_commit": estimator_commit,
        "attempted_candidates": attempted,
        "successful_candidates": successful,
        "covered_candidates": covered,
        "eligible_candidates": eligible,
        "message_codes": list(dict.fromkeys(message_codes or [])),
    }
