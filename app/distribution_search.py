"""Shared request and enumeration helpers for secret/error distributions.

The search implementations historically represented distributions in two
slightly different ways.  This module is deliberately independent of either
search implementation so that request validation and additive-composition
metadata can be shared without introducing an import cycle.

The public helpers return JSON-friendly dictionaries.  Scheme-specific code
may adapt those dictionaries to its existing ``DistributionSpec`` type while
the estimator layer can use the ``estimator`` member directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations_with_replacement
from typing import Any, Iterator, Sequence


SUPPORTED_DISTRIBUTION_SELECTORS = {
    "auto",
    "centered_binomial",
    "sparse_ternary",
}
SUPPORTED_DISTRIBUTION_MODES = {"pure", "combination"}
UNAVAILABLE_SAMPLING_BITS = 1_000_000.0
MIN_DISTRIBUTION_COMPONENTS = 1
MAX_DISTRIBUTION_COMPONENTS = 6
DEFAULT_DISTRIBUTION_COMPONENTS = 3
CBD_ETAS = (1, 2, 3, 4, 5, 6, 8)
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


@dataclass(frozen=True)
class DistributionRequest:
    """Validated distribution controls for one (Secret or Error) module."""

    mode: str = "pure"
    selector: str = "auto"
    max_components: int = DEFAULT_DISTRIBUTION_COMPONENTS

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().lower()
        if mode not in SUPPORTED_DISTRIBUTION_MODES:
            raise ValueError("distribution mode must be pure or combination.")
        if not isinstance(self.max_components, int) or isinstance(self.max_components, bool):
            raise ValueError(
                "max_distribution_components must be an integer between 1 and 6."
            )
        if not MIN_DISTRIBUTION_COMPONENTS <= self.max_components <= MAX_DISTRIBUTION_COMPONENTS:
            raise ValueError(
                "max_distribution_components must be an integer between 1 and 6."
            )

        selector = str(self.selector).strip().lower()
        # LWR uses the same request contract for its compression modulus.  It
        # is parsed by parameter_search, but accepting a decimal selector here
        # lets callers validate the mode and limit through this shared helper.
        if selector not in SUPPORTED_DISTRIBUTION_SELECTORS and not selector.isdigit():
            raise ValueError(
                "distribution selector must be one of auto, centered_binomial, sparse_ternary."
            )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "selector", selector)


def parse_distribution_request(
    raw: dict[str, Any] | None,
    prefix: str,
    *,
    lwr_error: bool = False,
) -> DistributionRequest:
    """Parse a Secret/Error module from snake_case or camelCase JSON.

    ``maxDistributionComponents`` is intentionally global: both modules use
    the same bound, while their modes and selectors remain independent.  For
    LWR-family requests Error is compression noise, so an explicit
    ``errorDistributionMode=combination`` is rejected instead of silently
    changing semantics.
    """

    payload = raw or {}
    key_prefix = str(prefix).strip()
    if not key_prefix:
        raise ValueError("distribution request prefix is required.")
    # JSON uses ``secretDistribution...`` / ``errorDistribution...`` (the
    # first word remains lower-case), while Python callers commonly use the
    # snake_case spelling.
    camel_prefix = key_prefix

    mode_value = payload.get(
        f"{key_prefix}_distribution_mode",
        payload.get(f"{camel_prefix}DistributionMode", "pure"),
    )
    mode = str(mode_value or "pure").strip().lower()
    if mode not in SUPPORTED_DISTRIBUTION_MODES:
        raise ValueError("distribution mode must be pure or combination.")
    if lwr_error and key_prefix.lower() == "error" and mode == "combination":
        raise ValueError(
            "LWR-style Error uses compression noise and does not support distribution combination."
        )

    selector_value = payload.get(
        f"{key_prefix}_distribution",
        payload.get(
            f"{camel_prefix}Distribution",
            payload.get("distribution", "auto"),
        ),
    )
    selector = str(selector_value or "auto").strip().lower()
    if not lwr_error and selector not in SUPPORTED_DISTRIBUTION_SELECTORS:
        if selector == "uniform":
            raise ValueError(
                f"{key_prefix}_distribution must be one of auto, centered_binomial, sparse_ternary. "
                "Uniform is not a secret/error selector for LWE-style searches."
            )
        raise ValueError(
            f"{key_prefix}_distribution must be one of auto, centered_binomial, sparse_ternary."
        )

    limit_value = payload.get(
        "max_distribution_components",
        payload.get("maxDistributionComponents", DEFAULT_DISTRIBUTION_COMPONENTS),
    )
    if isinstance(limit_value, bool):
        raise ValueError(
            "max_distribution_components must be an integer between 1 and 6."
        )
    try:
        # Do not accept 3.5 or strings such as "three".  JSON numbers may be
        # represented as int, while a numeric string is accepted for CLI/API
        # compatibility when it is exactly integral.
        if isinstance(limit_value, float) and not limit_value.is_integer():
            raise ValueError
        limit = int(limit_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "max_distribution_components must be an integer between 1 and 6."
        ) from exc
    if str(limit_value).strip() != str(limit) and not isinstance(limit_value, int):
        raise ValueError(
            "max_distribution_components must be an integer between 1 and 6."
        )

    return DistributionRequest(mode=mode, selector=selector, max_components=limit)


def centered_binomial_component(eta: int) -> dict[str, Any]:
    """Return one centered-binomial component in the shared JSON contract."""

    try:
        eta = int(eta)
    except (TypeError, ValueError) as exc:
        raise ValueError("centered-binomial eta must be a positive integer.") from exc
    if eta < 1:
        raise ValueError("centered-binomial eta must be a positive integer.")
    variance = eta / 2.0
    return {
        "family": "centered_binomial",
        "name": f"CBD({eta})",
        "parameters": {"eta": eta},
        "mean": 0.0,
        "variance": variance,
        "stddev": math.sqrt(variance),
        "support": [-eta, eta],
        "sampling_bits": 2 * eta,
        "symmetric": True,
        "sampling": "bit-sliced popcount friendly",
        "estimator": {"type": "centered_binomial", "eta": eta},
    }


def sparse_ternary_component(l0: int, l1: int, n: int) -> dict[str, Any]:
    """Return one sparse-ternary component for a polynomial of degree ``n``."""

    try:
        l0 = int(l0)
        l1 = int(l1)
        n = int(n)
    except (TypeError, ValueError) as exc:
        raise ValueError("sparse-ternary parameters must be integers.") from exc
    if l0 < 1 or l1 < 0 or n < 1:
        raise ValueError("sparse-ternary requires l0 >= 1, l1 >= 0, and n >= 1.")
    probability_each = ((2**l0) - 1) / (2 ** (2 * l0 + l1))
    plus_weight = max(0, round(n * probability_each))
    minus_weight = plus_weight
    if plus_weight < 1 or minus_weight < 1:
        raise ValueError("sparse-ternary component has no non-zero coefficients at this n.")
    variance = 2.0 * probability_each
    sampling_bits = 2 * l0 + l1
    return {
        "family": "sparse_ternary",
        "name": f"ST(l0={l0}, l1={l1})",
        "parameters": {
            "l0": l0,
            "l1": l1,
            "probability_plus": probability_each,
            "probability_minus": probability_each,
            "probability_zero": 1 - 2 * probability_each,
            "nonzero_probability": 2 * probability_each,
        },
        "mean": 0.0,
        "variance": variance,
        "stddev": math.sqrt(variance),
        "support": [-1, 0, 1],
        "sampling_bits": sampling_bits,
        "symmetric": True,
        "sampling": "sample sign/magnitude from bit arithmetic; zero otherwise",
        "estimator": {
            "type": "sparse_ternary_fixed_weight",
            "plus_weight": plus_weight,
            "minus_weight": minus_weight,
            "iid_stddev": math.sqrt(variance),
            "fixed_weight_stddev": math.sqrt((plus_weight + minus_weight) / n),
            "sampling_bits": sampling_bits,
            "note": "fixed-weight approximation to the iid sparse ternary distribution",
        },
    }


def compose_distribution(components: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Compose independent additive components using a moment approximation."""

    if not components:
        raise ValueError("at least one distribution component is required.")
    copied = [dict(component) for component in components]
    if len(copied) == 1:
        component = copied[0]
        return {
            **component,
            "family": "pure",
            "component_family": component.get("family"),
            "component_count": 1,
            "components": [component_summary(component)],
            "estimator": dict(component.get("estimator", {})),
        }

    mean = sum(float(component.get("mean", 0.0)) for component in copied)
    variance = sum(float(component.get("variance", 0.0)) for component in copied)
    support_low = sum(numeric_support(component)[0] for component in copied)
    support_high = sum(numeric_support(component)[1] for component in copied)
    stddev = math.sqrt(max(0.0, variance))
    sampling_bits = sum(
        int(component.get("sampling_bits", 0))
        for component in copied
        if component.get("sampling_bits") is not None
    )
    names = [str(component.get("name", component.get("family", "component"))) for component in copied]
    components_summary = [component_summary(component) for component in copied]
    return {
        "family": "composite",
        "name": " + ".join(names),
        "parameters": {"components": [component.get("parameters", {}) for component in copied]},
        "mean": round(mean, 9),
        "variance": round(variance, 9),
        "stddev": round(stddev, 9),
        "support": [support_low, support_high],
        "sampling_bits": sampling_bits,
        "symmetric": all(bool(component.get("symmetric", False)) for component in copied),
        "sampling": "sample each listed component independently and add the coefficients",
        "component_count": len(copied),
        "components": components_summary,
        "estimator": {
            "type": "composite_moment",
            "mean": mean,
            "stddev": stddev,
            "bounds": [support_low, support_high],
            "components": components_summary,
            "note": (
                "conservative moment approximation for an additive distribution; "
                "not a sampler or scheme-level security proof"
            ),
        },
        "warnings": [
            "Composite distribution uses a conservative moment approximation for estimator validation."
        ],
    }


def enumerate_distribution_candidates(
    n: int,
    request: DistributionRequest,
) -> Iterator[dict[str, Any]]:
    """Yield pure or additive distributions in deterministic order."""

    if not isinstance(request, DistributionRequest):
        raise TypeError("request must be a DistributionRequest.")
    components = _base_components(n, request.selector)
    if request.mode == "pure":
        for component in components:
            yield compose_distribution([component])
        return

    candidates = []
    for size in range(1, request.max_components + 1):
        for selected in combinations_with_replacement(components, size):
            candidates.append(compose_distribution(selected))
    yield from sorted(candidates, key=distribution_order_key)


def distribution_order_key(distribution: dict[str, Any]) -> tuple[Any, ...]:
    """Return a stable key: pure first, then component count and metadata."""

    family = str(distribution.get("family", ""))
    family_rank = {"pure": 0, "composite": 1}.get(family, 2)
    count = int(distribution.get("component_count", len(distribution.get("components", [])) or 1))
    components = distribution.get("components", [])
    component_names = tuple(str(item.get("name", "")) for item in components)
    component_families = tuple(
        _component_family_rank(str(item.get("family", "")))
        for item in components
    )
    if not component_families:
        component_family = distribution.get("component_family", family)
        component_families = (_component_family_rank(str(component_family)),)
    sampling_bits = distribution.get("sampling_bits")
    try:
        sampling_bits = float(sampling_bits)
        if not math.isfinite(sampling_bits):
            raise ValueError
    except (TypeError, ValueError):
        sampling_bits = UNAVAILABLE_SAMPLING_BITS
    return (
        family_rank,
        sampling_bits,
        count,
        component_families,
        component_names,
        float(distribution.get("variance", 0.0)),
        str(distribution.get("name", "")),
    )


def _base_components(n: int, selector: str) -> list[dict[str, Any]]:
    selector = str(selector).strip().lower()
    components: list[dict[str, Any]] = []
    if selector in {"auto", "centered_binomial"}:
        components.extend(centered_binomial_component(eta) for eta in CBD_ETAS)
    if selector in {"auto", "sparse_ternary"}:
        for l0, l1 in SPARSE_TERNARY_PARAMETERS:
            try:
                components.append(sparse_ternary_component(l0, l1, n))
            except ValueError:
                continue
    if selector not in SUPPORTED_DISTRIBUTION_SELECTORS:
        raise ValueError(
            "distribution selector must be one of auto, centered_binomial, sparse_ternary."
        )
    return components


def component_summary(component: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": component.get("family"),
        "name": component.get("name"),
        "parameters": component.get("parameters", {}),
        "mean": component.get("mean", 0.0),
        "variance": component.get("variance", 0.0),
        "stddev": component.get("stddev", 0.0),
        "support": component.get("support", [-1, 1]),
        "sampling_bits": component.get("sampling_bits"),
        "estimator": component.get("estimator", {}),
    }


def numeric_support(distribution: dict[str, Any]) -> tuple[int, int]:
    support = distribution.get("support", [-1, 1])
    if not isinstance(support, (list, tuple)) or len(support) != 2:
        return (-1, 1)
    try:
        return int(support[0]), int(support[1])
    except (TypeError, ValueError):
        return (-1, 1)


def _component_family_rank(family: str) -> int:
    # When both families consume the same number of random bits, prefer the
    # explicitly ternary sampler so its {-1, 0, +1} support remains visible in
    # the automatic recommendation.
    return {"sparse_ternary": 0, "centered_binomial": 1}.get(family, 2)


__all__ = [
    "CBD_ETAS",
    "DEFAULT_DISTRIBUTION_COMPONENTS",
    "DistributionRequest",
    "MAX_DISTRIBUTION_COMPONENTS",
    "MIN_DISTRIBUTION_COMPONENTS",
    "SPARSE_TERNARY_PARAMETERS",
    "SUPPORTED_DISTRIBUTION_MODES",
    "SUPPORTED_DISTRIBUTION_SELECTORS",
    "centered_binomial_component",
    "compose_distribution",
    "distribution_order_key",
    "enumerate_distribution_candidates",
    "parse_distribution_request",
    "sparse_ternary_component",
]
