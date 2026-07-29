from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .config import configured_estimator_source_root, read_json


PROFILE_PATH_FIELDS = (
    "lattice_estimator_path",
    "enhanced_lattice_estimator_path",
)


class SetupConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SetupConfigUpdate:
    action: Literal["created", "updated", "unchanged", "regenerated"]
    supplemented_fields: tuple[str, ...]
    preserved_invalid_fields: tuple[str, ...]


def _normalized_candidate(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return str(Path(value).expanduser().resolve())


def default_setup_config(
    sage_binary: str,
    standard_path: str | None,
    enhanced_path: str | None,
) -> dict[str, Any]:
    return {
        "estimator": {
            "sage_binary": sage_binary,
            "lattice_estimator_path": _normalized_candidate(standard_path),
            "enhanced_lattice_estimator_path": _normalized_candidate(enhanced_path),
            "default_timeout_seconds": 16,
            "per_attack_timeout_seconds": 12,
            "remote_url": None,
            "remote_timeout_seconds": 240,
            "remote_poll_interval_seconds": 2,
        },
        "llm": {
            "enabled": False,
            "provider": "openai-compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "local-model",
            "api_key_env": "EASYLATTICE_LLM_API_KEY",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer ",
            "timeout_seconds": 30,
        },
        "scripts": {
            "decrypt_error": [],
            "signature_smoothing": [],
        },
    }


def _is_empty(value: object) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def _valid_profile_path(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    root = configured_estimator_source_root(value)
    return bool(root and (root / "estimator" / "__init__.py").is_file())


def _read_existing(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise SetupConfigError("Local configuration must contain a JSON object.")
    estimator = value.get("estimator")
    if estimator is not None and not isinstance(estimator, dict):
        raise SetupConfigError(
            "Local estimator configuration must contain a JSON object."
        )
    return value


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except Exception as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise SetupConfigError(
            f"Could not update local configuration: {type(exc).__name__}: {exc}"
        ) from exc


def update_setup_config(
    path: Path,
    sage_binary: str,
    standard_path: str | None,
    enhanced_path: str | None,
    *,
    force: bool = False,
) -> SetupConfigUpdate:
    path = path.expanduser()
    existed = path.exists()
    candidates = {
        "lattice_estimator_path": _normalized_candidate(standard_path),
        "enhanced_lattice_estimator_path": _normalized_candidate(enhanced_path),
    }
    if force or not existed:
        value = default_setup_config(sage_binary, standard_path, enhanced_path)
        _atomic_write(path, value)
        supplemented = tuple(
            field for field in PROFILE_PATH_FIELDS if candidates[field]
        )
        return SetupConfigUpdate(
            action="regenerated" if existed else "created",
            supplemented_fields=supplemented,
            preserved_invalid_fields=(),
        )

    value = _read_existing(path)
    estimator = value.get("estimator")
    if estimator is None:
        estimator = {}
        value["estimator"] = estimator

    supplemented_fields: list[str] = []
    for field in PROFILE_PATH_FIELDS:
        if _is_empty(estimator.get(field)) and candidates[field] is not None:
            estimator[field] = candidates[field]
            supplemented_fields.append(field)

    preserved_invalid_fields = tuple(
        field
        for field in PROFILE_PATH_FIELDS
        if not _is_empty(estimator.get(field))
        and not _valid_profile_path(estimator.get(field))
    )
    if supplemented_fields:
        _atomic_write(path, value)
        action: Literal["updated", "unchanged"] = "updated"
    else:
        action = "unchanged"
    return SetupConfigUpdate(
        action=action,
        supplemented_fields=tuple(supplemented_fields),
        preserved_invalid_fields=preserved_invalid_fields,
    )


def main() -> None:
    path = Path(os.environ["EASYLATTICE_SETUP_CONFIG"])
    result = update_setup_config(
        path,
        os.environ["EASYLATTICE_SETUP_SAGE"],
        os.environ.get("EASYLATTICE_SETUP_ESTIMATOR") or None,
        os.environ.get("EASYLATTICE_SETUP_ENHANCED_ESTIMATOR") or None,
        force=os.environ.get("EASYLATTICE_SETUP_FORCE") == "1",
    )
    print(f"Configuration {result.action}: {path}")
    if result.supplemented_fields:
        print("Supplemented profile fields: " + ", ".join(result.supplemented_fields))
    if result.preserved_invalid_fields:
        print(
            "Preserved non-empty invalid profile fields: "
            + ", ".join(result.preserved_invalid_fields)
        )


if __name__ == "__main__":
    main()
