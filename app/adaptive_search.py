"""Shared target-aware candidate validation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator


@dataclass
class AdaptiveValidationResult:
    """Accounting returned by :func:`adaptive_validate`."""

    attempted: int = 0
    successful: int = 0
    validated: list[dict[str, Any]] = field(default_factory=list)
    target_met: bool = False
    exhausted: bool = False
    status: str = "not_requested"
    best_candidate: dict[str, Any] | None = None


def adaptive_validate(
    candidates: Iterable[dict[str, Any]],
    *,
    estimate: Callable[[dict[str, Any]], Any],
    normalize: Callable[[dict[str, Any], Any], tuple[Any | None, dict[str, Any]]],
    apply: Callable[[dict[str, Any], Any], None],
    meets_target: Callable[[dict[str, Any]], bool],
    order_key: Callable[[dict[str, Any]], Any] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> AdaptiveValidationResult:
    """Validate candidates in order until the first measured target hit.

    ``normalize`` returns the scheme-specific normalized estimator result and a
    JSON-safe validation entry. A ``None`` normalized result is recorded as a
    failed attempt and does not stop the iterator. ``apply`` mutates the
    candidate with a successful estimator result, allowing the caller's target
    predicate and reference ranking to use measured security values.
    """

    ordered: Iterator[dict[str, Any]]
    if order_key is None:
        ordered = iter(candidates)
    else:
        ordered = iter(sorted(candidates, key=order_key))

    result = AdaptiveValidationResult(status="running")
    checked: list[dict[str, Any]] = []

    for candidate in ordered:
        if cancel is not None and cancel():
            result.status = "cancelled"
            break

        result.attempted += 1
        if on_progress is not None:
            on_progress(
                {
                    "event": "candidate_started",
                    "candidate": candidate,
                    "attempted": result.attempted,
                }
            )
        try:
            raw = estimate(candidate)
            normalized, entry = normalize(candidate, raw)
        except Exception as exc:  # callers retain the exception in validation metadata
            normalized = None
            entry = {
                "ok": False,
                "code": "candidate_validation_failed",
                "message": f"{type(exc).__name__}: {exc}",
            }

        result.validated.append(entry)
        if normalized is None:
            if on_progress is not None:
                on_progress(
                    {
                        "event": "candidate_failed",
                        "candidate": candidate,
                        "attempted": result.attempted,
                    }
                )
            continue

        result.successful += 1
        apply(candidate, normalized)
        checked.append(candidate)
        result.best_candidate = candidate
        if on_progress is not None:
            on_progress(
                {
                    "event": "candidate_completed",
                    "candidate": candidate,
                    "attempted": result.attempted,
                    "successful": result.successful,
                }
            )
        if meets_target(candidate):
            result.target_met = True
            result.status = "target_met"
            result.best_candidate = candidate
            break
    else:
        result.exhausted = True
        result.status = "no_feasible_candidate" if checked else "target_unmet"

    if not result.target_met and result.status == "running":
        result.status = "target_unmet"
    return result
