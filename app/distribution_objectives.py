"""Objective tables for Secret/Error distribution selection.

Sampling cost and distribution width are deliberately kept as separate
objectives.  They are not interchangeable: for example, a sparse-ternary
row can spend more sampling bits while having a *smaller* variance, whereas a
centered-binomial row normally has the opposite trend.

The helpers in this module are scheme agnostic.  They operate on the JSON
candidate shape produced by both the RLWE and NTRU searchers and therefore
keep the two search implementations consistent.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Callable, Iterable


UNAVAILABLE_DISTRIBUTION_METRIC = 1_000_000.0
MIN_SAMPLING_BITS_OBJECTIVE = "min_sampling_bits"
MIN_STDDEV_OBJECTIVE = "min_stddev"


def candidate_modulus_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    """Return the fixed ring/modulus key used before distribution selection."""

    ring = candidate.get("ring") or {}
    modulus = candidate.get("modulus") or {}
    return (
        str(ring.get("family_id", ring.get("family", ""))),
        int(ring.get("n", 0)),
        int(modulus.get("q", 0)),
    )


def _metric(distribution: dict[str, Any] | None, field: str) -> float:
    if not isinstance(distribution, dict):
        return UNAVAILABLE_DISTRIBUTION_METRIC
    try:
        value = float(distribution.get(field))
    except (TypeError, ValueError):
        return UNAVAILABLE_DISTRIBUTION_METRIC
    return value if math.isfinite(value) and value >= 0 else UNAVAILABLE_DISTRIBUTION_METRIC


def distribution_metrics(candidate: dict[str, Any]) -> dict[str, float]:
    """Return comparable Secret/Error sampling and width metrics.

    ``combined_stddev`` is the standard deviation of the independent sum
    (equivalently ``sqrt(sigma_s^2 + sigma_e^2)``).  Keeping the component
    values in the table makes the tie-breaks auditable in the API response.
    """

    distribution = candidate.get("distribution") or {}
    secret = distribution.get("secret") or {}
    error = distribution.get("error") or {}
    secret_bits = _metric(secret, "sampling_bits")
    error_bits = _metric(error, "sampling_bits")
    # LWR/RLWR/MLWR Error is a fixed q->p compression law rather than a
    # user-sampled distribution.  It contributes no independent sampler
    # bits, so Secret remains the active sampling-cost objective.
    if str(error.get("family", "")) == "compression_noise" and error_bits >= UNAVAILABLE_DISTRIBUTION_METRIC:
        error_bits = 0.0
    secret_stddev = _metric(secret, "stddev")
    error_stddev = _metric(error, "stddev")
    if secret_stddev >= UNAVAILABLE_DISTRIBUTION_METRIC or error_stddev >= UNAVAILABLE_DISTRIBUTION_METRIC:
        combined_stddev = UNAVAILABLE_DISTRIBUTION_METRIC
    else:
        combined_stddev = math.hypot(secret_stddev, error_stddev)
    return {
        "secret_sampling_bits": secret_bits,
        "error_sampling_bits": error_bits,
        "total_sampling_bits": secret_bits + error_bits,
        "secret_stddev": secret_stddev,
        "error_stddev": error_stddev,
        "combined_stddev": combined_stddev,
    }


def _name(candidate: dict[str, Any], side: str) -> str:
    distribution = (candidate.get("distribution") or {}).get(side) or {}
    return str(distribution.get("name", ""))


def _family_rank(candidate: dict[str, Any], side: str) -> int:
    distribution = (candidate.get("distribution") or {}).get(side) or {}
    family = str(distribution.get("family", distribution.get("component_family", "")))
    # Preserve the existing automatic preference for a sparse ternary
    # sampler when all objective metrics tie exactly.
    return {"sparse_ternary": 0, "sparse_ternary_fixed_weight": 0, "centered_binomial": 1}.get(family, 2)


def distribution_objective_key(
    candidate: dict[str, Any],
    objective: str = MIN_SAMPLING_BITS_OBJECTIVE,
) -> tuple[Any, ...]:
    """Return a deterministic within-``n,q`` key for one objective."""

    metrics = distribution_metrics(candidate)
    if objective == MIN_SAMPLING_BITS_OBJECTIVE:
        return (
            metrics["total_sampling_bits"],
            metrics["combined_stddev"],
            metrics["secret_sampling_bits"],
            metrics["error_sampling_bits"],
            _family_rank(candidate, "secret"),
            _family_rank(candidate, "error"),
            _name(candidate, "secret"),
            _name(candidate, "error"),
        )
    if objective == MIN_STDDEV_OBJECTIVE:
        return (
            metrics["combined_stddev"],
            metrics["secret_stddev"],
            metrics["error_stddev"],
            metrics["total_sampling_bits"],
            metrics["secret_sampling_bits"],
            metrics["error_sampling_bits"],
            _family_rank(candidate, "secret"),
            _family_rank(candidate, "error"),
            _name(candidate, "secret"),
            _name(candidate, "error"),
        )
    raise ValueError(f"unknown distribution objective: {objective}")


def distribution_table(
    candidates: Iterable[dict[str, Any]],
    *,
    include_rows: bool = False,
) -> dict[str, Any]:
    """Build the two precomputed orderings used by the selector.

    By default the result is a compact summary suitable for the API response.
    ``include_rows=True`` additionally returns stable metadata rows and the
    two candidate indexes; those rows never duplicate complete candidates.
    """

    rows = list(candidates)
    indexed = []
    for index, candidate in enumerate(rows):
        metrics = distribution_metrics(candidate)
        indexed.append(
            {
                "index": index,
                "modulus": candidate_modulus_key(candidate),
                **metrics,
            }
        )
    by_sampling = sorted(
        indexed,
        key=lambda row: (
            row["modulus"],
            row["total_sampling_bits"],
            row["secret_sampling_bits"],
            row["error_sampling_bits"],
            row["index"],
        ),
    )
    by_stddev = sorted(
        indexed,
        key=lambda row: (
            row["modulus"],
            row["combined_stddev"],
            row["secret_stddev"],
            row["error_stddev"],
            row["index"],
        ),
    )
    groups = defaultdict(int)
    for row in indexed:
        groups[row["modulus"]] += 1
    result = {
        "group_count": len(groups),
        "candidate_count": len(rows),
        "group_sizes": [
            {"modulus": list(key), "count": value}
            for key, value in groups.items()
        ],
        "sampling_order_count": len(by_sampling),
        "stddev_order_count": len(by_stddev),
    }
    if include_rows:
        result["rows"] = indexed
        result["by_sampling_bits"] = [row["index"] for row in by_sampling]
        result["by_stddev"] = [row["index"] for row in by_stddev]
    return result


def _stddev_order(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda candidate: distribution_objective_key(candidate, MIN_STDDEV_OBJECTIVE),
    )


def minimum_stddev_target(
    candidates: Iterable[dict[str, Any]],
    *,
    meets_target: Callable[[dict[str, Any]], bool],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Find the first target row in the standard-deviation table.

    A binary search is valid only when the target predicate is false-to-true
    monotone in the table order.  The predicate is checked for every row that
    is already available to this selector; if that sequence is not monotone,
    the function performs an exact linear fallback.  In estimator mode the
    caller may pass a partially validated table; the returned metadata makes
    that fact explicit instead of presenting a heuristic as a proof.
    """

    ordered = _stddev_order(candidates)
    if not ordered:
        return None, {
            "method": "empty_table",
            "monotone": True,
            "evaluated_rows": 0,
        }

    statuses = [bool(meets_target(candidate)) for candidate in ordered]
    monotone = all(not statuses[index] or statuses[index + 1] for index in range(len(statuses) - 1))
    if not monotone:
        for candidate, status in zip(ordered, statuses):
            if status:
                return candidate, {
                    "method": "linear_fallback_nonmonotone",
                    "monotone": False,
                    "evaluated_rows": len(ordered),
                }
        return None, {
            "method": "linear_fallback_nonmonotone",
            "monotone": False,
            "evaluated_rows": len(ordered),
        }

    # The table is already materialized, so the status checks above are
    # cheap.  The binary search documents and exercises the intended search
    # boundary while preserving exactness for the available rows.
    first_true = None
    low, high = 0, len(ordered) - 1
    if statuses[high]:
        while low <= high:
            middle = (low + high) // 2
            if statuses[middle]:
                first_true = middle
                high = middle - 1
            else:
                low = middle + 1
    return (
        (ordered[first_true] if first_true is not None else None),
        {
            "method": "binary_search_monotone_stddev",
            "monotone": True,
            "evaluated_rows": len(ordered),
            "boundary_index": first_true,
        },
    )


def select_distribution_objectives(
    candidates: Iterable[dict[str, Any]],
    *,
    meets_target: Callable[[dict[str, Any]], bool],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    """Select both distribution objectives after the first feasible modulus.

    Modulus order remains authoritative: the first ``(n,q)`` group containing
    a target hit wins.  Only then are the two independent distribution tables
    applied inside that group.  If no target is available, the same operation
    is performed on the first group in deterministic order and its strongest
    available rows are returned as best-effort references.
    """

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    group_order: dict[tuple[Any, ...], int] = {}
    for candidate in candidates:
        key = candidate_modulus_key(candidate)
        group_order.setdefault(key, len(group_order))
        grouped[key].append(candidate)
    if not grouped:
        return None, None, {
            "objective": "distribution_table",
            "method": "precomputed_two_order_tables",
            "group_count": 0,
            "candidate_count": 0,
            "target_group": None,
        }

    # The caller supplies candidates in the authoritative ring/n/q order.  Do
    # not replace it with lexical family ordering (which would make an
    # ``auto`` family request prefer e.g. HPS over a smaller power-of-two n).
    groups = sorted(grouped.items(), key=lambda item: group_order[item[0]])
    selected_group: list[dict[str, Any]] | None = None
    selected_key: tuple[Any, ...] | None = None
    target_available = False
    for key, group in groups:
        if any(meets_target(candidate) for candidate in group):
            selected_key = key
            selected_group = group
            target_available = True
            break
    if selected_group is None:
        selected_key, selected_group = groups[0]

    hits = [candidate for candidate in selected_group if meets_target(candidate)]
    secondary, stddev_search = minimum_stddev_target(
        selected_group,
        meets_target=meets_target,
    )
    pool = hits or selected_group
    if secondary is None:
        # No measured target is available.  Keep the best-effort response
        # deterministic while retaining the same two-stage objective shape.
        secondary = min(pool, key=lambda candidate: distribution_objective_key(candidate, MIN_STDDEV_OBJECTIVE))
    threshold = distribution_metrics(secondary)["combined_stddev"]
    threshold_pool = [
        candidate
        for candidate in pool
        if distribution_metrics(candidate)["combined_stddev"] + 1e-12 >= threshold
    ]
    primary = min(
        threshold_pool or pool,
        key=lambda candidate: distribution_objective_key(candidate, MIN_SAMPLING_BITS_OBJECTIVE),
    )
    metadata = {
        "objective": "distribution_table",
        "method": "precomputed_two_order_tables",
        "group_count": len(groups),
        "candidate_count": sum(len(group) for _, group in groups),
        "target_group": list(selected_key) if selected_key is not None else None,
        "target_group_candidate_count": len(selected_group),
        "target_group_hit_count": len(hits),
        "target_met": target_available,
        "primary_objective": MIN_SAMPLING_BITS_OBJECTIVE,
        "secondary_objective": MIN_STDDEV_OBJECTIVE,
        "secondary_is_distinct": primary is not secondary,
        "stddev_threshold": threshold,
        "stddev_search": stddev_search,
        "sampling_search": "ordered_scan_above_stddev_threshold",
        "primary_metrics": distribution_metrics(primary),
        "secondary_metrics": distribution_metrics(secondary),
    }
    return primary, secondary, metadata


__all__ = [
    "MIN_SAMPLING_BITS_OBJECTIVE",
    "MIN_STDDEV_OBJECTIVE",
    "UNAVAILABLE_DISTRIBUTION_METRIC",
    "candidate_modulus_key",
    "distribution_metrics",
    "distribution_objective_key",
    "distribution_table",
    "select_distribution_objectives",
]
