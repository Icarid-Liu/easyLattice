from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import islice
from typing import Any


MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 4096
MAX_DIAGNOSTIC_LENGTH = 4096


@dataclass
class _Budget:
    remaining: int

    def consume(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def sanitize_json_value(
    value: Any,
    *,
    max_depth: int = MAX_JSON_DEPTH,
    max_items: int = MAX_JSON_ITEMS,
) -> Any:
    budget = _Budget(max(1, int(max_items)))
    return _sanitize(value, set(), 0, max(0, int(max_depth)), budget)


def _sanitize(
    value: Any,
    seen: set[int],
    depth: int,
    max_depth: int,
    budget: _Budget,
) -> Any:
    if not budget.consume():
        return "<maximum-items-exceeded>"
    if depth > max_depth:
        return "<maximum-depth-exceeded>"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _bounded_string(value)
    if isinstance(value, int):
        return value if value.bit_length() <= 4096 else "<integer-too-large>"
    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            return "<recursive-reference>"
        seen.add(identity)
        result: dict[str, Any] = {}
        try:
            for key, item in value.items():
                if budget.remaining <= 0:
                    result["<truncated>"] = "<maximum-items-exceeded>"
                    break
                result[sanitize_json_key(key)] = _sanitize(
                    item,
                    seen,
                    depth + 1,
                    max_depth,
                    budget,
                )
        finally:
            seen.remove(identity)
        return result

    if isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in seen:
            return "<recursive-reference>"
        seen.add(identity)
        result: list[Any] = []
        try:
            limit = max(0, budget.remaining)
            items = list(islice(iter(value), limit + 1))
            if isinstance(value, (set, frozenset)):
                items.sort(key=safe_diagnostic_string)
            truncated = len(items) > limit
            for item in items[:limit]:
                result.append(
                    _sanitize(item, seen, depth + 1, max_depth, budget)
                )
                if budget.remaining <= 0:
                    truncated = True
                    break
            if truncated:
                result.append("<maximum-items-exceeded>")
        finally:
            seen.remove(identity)
        return result
    return safe_diagnostic_string(value)


def sanitize_json_key(key: Any) -> str:
    if isinstance(key, str):
        return _bounded_string(key)
    if isinstance(key, bool):
        return "true" if key else "false"
    if isinstance(key, int):
        return str(key) if key.bit_length() <= 4096 else "<integer-key-too-large>"
    if isinstance(key, float) and not math.isfinite(key):
        return "<nonfinite-key>"
    return safe_diagnostic_string(key)


def safe_diagnostic_string(value: Any) -> str:
    try:
        rendered = str(value)
    except Exception:
        rendered = f"<{type(value).__name__}>"
    return _bounded_string(rendered)


def _bounded_string(value: str) -> str:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        value = value.encode("utf-8", errors="backslashreplace").decode("utf-8")
    if len(value) > MAX_DIAGNOSTIC_LENGTH:
        return value[:MAX_DIAGNOSTIC_LENGTH] + "<truncated>"
    return value


def reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")
