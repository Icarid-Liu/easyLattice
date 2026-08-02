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
    method: str = "ordered_scan"


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


def adaptive_binary_validate(
    candidates: Iterable[dict[str, Any]],
    *,
    estimate: Callable[[dict[str, Any]], Any],
    normalize: Callable[[dict[str, Any], Any], tuple[Any | None, dict[str, Any]]],
    apply: Callable[[dict[str, Any], Any], None],
    meets_target: Callable[[dict[str, Any]], bool],
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> AdaptiveValidationResult:
    """Validate the first target in a monotone ordered candidate table.

    ``candidates`` must be ordered by increasing distribution standard
    deviation.  Endpoint misses are not treated as proof of monotonicity: the
    helper falls back to an exact ordered scan.  When the high endpoint hits,
    the boundary is found with cached midpoint evaluations.  This is a
    deliberately small optimization layer around the existing validation
    contract; callers can still inspect ``attempted`` and every normalized
    failure through the returned result.
    """

    ordered = list(candidates)
    result = AdaptiveValidationResult(status="running")
    cache: dict[int, bool] = {}

    def evaluate(index: int) -> bool | None:
        if index in cache:
            status = cache[index]
            if status:
                result.best_candidate = ordered[index]
            return status
        if cancel is not None and cancel():
            result.status = "cancelled"
            return None
        candidate = ordered[index]
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
        except Exception as exc:
            normalized = None
            entry = {
                "ok": False,
                "code": "candidate_validation_failed",
                "message": f"{type(exc).__name__}: {exc}",
            }
        result.validated.append(entry)
        if normalized is None:
            cache[index] = False
            if on_progress is not None:
                on_progress(
                    {
                        "event": "candidate_failed",
                        "candidate": candidate,
                        "attempted": result.attempted,
                    }
                )
            return False
        result.successful += 1
        apply(candidate, normalized)
        result.best_candidate = candidate
        status = bool(meets_target(candidate))
        cache[index] = status
        if on_progress is not None:
            on_progress(
                {
                    "event": "candidate_completed",
                    "candidate": candidate,
                    "attempted": result.attempted,
                    "successful": result.successful,
                }
            )
        return status

    if not ordered:
        result.exhausted = True
        result.status = "no_feasible_candidate"
        result.method = "empty_table"
        return result

    first = evaluate(0)
    if first is None:
        return result
    if first:
        result.target_met = True
        result.status = "target_met"
        result.method = "binary_search_boundary_zero"
        return result

    last = evaluate(len(ordered) - 1)
    if last is None:
        return result

    if not last:
        # A target may still occur in the interior under a non-monotone
        # estimator.  Scan exactly rather than returning a false negative.
        for index in range(1, len(ordered) - 1):
            status = evaluate(index)
            if status is None:
                return result
            if status:
                result.target_met = True
                result.status = "target_met"
                result.method = "linear_fallback_endpoint_miss"
                return result
        result.exhausted = True
        result.status = "no_feasible_candidate" if result.successful else "target_unmet"
        result.method = "linear_fallback_endpoint_miss"
        return result

    low, high = 0, len(ordered) - 1
    while low < high:
        middle = (low + high) // 2
        status = evaluate(middle)
        if status is None:
            return result
        if status:
            high = middle
        else:
            low = middle + 1
    boundary = evaluate(low)
    if boundary:
        result.target_met = True
        result.status = "target_met"
        result.method = "binary_search_monotone_assumption"
        return result

    # A non-monotone sequence can defeat midpoint search.  Complete the
    # ordered scan while reusing all midpoint/endpoint evaluations.
    for index in range(len(ordered)):
        status = evaluate(index)
        if status is None:
            return result
        if status:
            result.target_met = True
            result.status = "target_met"
            result.method = "linear_fallback_boundary_miss"
            return result
    result.exhausted = True
    result.status = "no_feasible_candidate" if result.successful else "target_unmet"
    result.method = "linear_fallback_boundary_miss"
    return result
