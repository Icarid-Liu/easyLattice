from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    ROOT,
    AppConfig,
    EstimatorConfig,
    configured_estimator_source_root,
    load_config,
    read_json,
)


PROFILE_FIELDS = frozenset(
    {
        "sage_binary",
        "lattice_estimator_path",
        "enhanced_lattice_estimator_path",
    }
)
PROFILE_VALUE_MAX_CHARS = 4096
STANDARD_VARIANTS = frozenset({"lwe", "lwr"})
ENHANCED_VARIANTS = frozenset({"rlwe", "mlwe", "rlwr", "mlwr"})
_PROFILE_WRITE_LOCK = threading.Lock()

ESTIMATOR_ORIGIN_PREFLIGHT = r"""
import json
import sys
from pathlib import Path

expected_root = Path(sys.argv[1]).resolve()
application_root = Path(sys.argv[2]).resolve()
if str(application_root) not in sys.path:
    sys.path.insert(0, str(application_root))

try:
    import estimator

    origin = Path(estimator.__file__).resolve()
    actual_root = origin.parent.parent if origin.parent.name == "estimator" else origin.parent
    actual_root = actual_root.resolve()
except Exception as exc:
    result = {
        "ok": False,
        "code": "estimator_origin_mismatch",
        "message": f"Could not import the selected estimator: {type(exc).__name__}: {exc}",
    }
else:
    if actual_root == expected_root:
        result = {"ok": True}
    else:
        result = {
            "ok": False,
            "code": "estimator_origin_mismatch",
            "message": f"Estimator imported from {actual_root}, expected {expected_root}.",
        }

print(json.dumps(result))
"""


class LocalProfileError(ValueError):
    def __init__(self, code: str, message: str, **details: object):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_result(self) -> dict[str, object]:
        return {"ok": False, "code": self.code, "message": self.message, **self.details}

    def as_api_payload(self) -> dict[str, object]:
        return {"ok": False, "code": self.code, "error": self.message, **self.details}


@dataclass(frozen=True)
class LocalProfileInput:
    sage_binary: str
    lattice_estimator_path: str
    enhanced_lattice_estimator_path: str | None


@dataclass(frozen=True)
class EstimatorRuntime:
    sage_binary: str
    root: Path
    environment: dict[str, str]


@dataclass(frozen=True)
class GitMetadata:
    commit: str | None
    dirty: bool | None
    message: str | None


def _invalid_request(message: str, **details: object) -> LocalProfileError:
    return LocalProfileError("invalid_profile_request", message, **details)


def _clean_string(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise _invalid_request(f"{field} must be a string.", field=field)
    if len(value) > PROFILE_VALUE_MAX_CHARS:
        raise _invalid_request(
            f"{field} must not exceed {PROFILE_VALUE_MAX_CHARS} characters.",
            field=field,
        )
    if "\0" in value:
        raise _invalid_request(f"{field} must not contain NUL bytes.", field=field)

    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    if cleaned.startswith(("'", '"')) or cleaned.endswith(("'", '"')):
        raise _invalid_request(f"{field} contains malformed surrounding quotes.", field=field)
    if not cleaned:
        return None if nullable else ""
    return str(Path(cleaned).expanduser()) if cleaned.startswith("~") else cleaned


def _normalize_estimator_path(
    cleaned: str | None,
    field: str,
    *,
    nullable: bool,
) -> str | None:
    if not cleaned:
        if nullable:
            return None
        raise _invalid_request(f"{field} is required.", field=field)

    root = configured_estimator_source_root(cleaned)
    if root is None or not (root / "estimator" / "__init__.py").is_file():
        raise LocalProfileError(
            "estimator_path_invalid",
            f"{field} must contain estimator/__init__.py.",
            field=field,
            path=str(root) if root else cleaned,
        )
    return str(root)


def parse_profile_request(payload: object) -> LocalProfileInput:
    if not isinstance(payload, dict):
        raise _invalid_request("Profile request must be a JSON object.")

    unknown_fields = sorted(set(payload) - PROFILE_FIELDS, key=str)
    if unknown_fields:
        raise _invalid_request(
            "Profile request contains unknown fields.",
            unknown_fields=unknown_fields,
        )

    sage_binary = _clean_string(payload.get("sage_binary"), "sage_binary")
    standard_value = _clean_string(
        payload.get("lattice_estimator_path"),
        "lattice_estimator_path",
    )
    enhanced_value = _clean_string(
        payload.get("enhanced_lattice_estimator_path"),
        "enhanced_lattice_estimator_path",
        nullable=True,
    )
    if not sage_binary:
        raise _invalid_request("sage_binary is required.", field="sage_binary")

    standard = _normalize_estimator_path(
        standard_value,
        "lattice_estimator_path",
        nullable=False,
    )
    enhanced = _normalize_estimator_path(
        enhanced_value,
        "enhanced_lattice_estimator_path",
        nullable=True,
    )
    assert standard is not None
    return LocalProfileInput(
        sage_binary=sage_binary,
        lattice_estimator_path=standard,
        enhanced_lattice_estimator_path=enhanced,
    )


def _profile_path(estimator: EstimatorConfig, profile: str) -> str | None:
    if profile == "standard":
        return estimator.lattice_estimator_path
    if profile == "enhanced":
        return estimator.enhanced_lattice_estimator_path
    raise _invalid_request(
        "Estimator profile must be standard or enhanced.",
        profile=profile,
    )


def _resolve_sage_binary(value: str) -> str:
    located = shutil.which(value)
    if located:
        return located

    candidate = Path(value).expanduser()
    if not candidate.exists():
        raise LocalProfileError(
            "sage_not_found",
            f"Sage binary '{value}' was not found.",
        )
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise LocalProfileError(
            "sage_not_executable",
            f"Sage binary '{value}' is not an executable file.",
        )
    return str(candidate.resolve())


def prepare_estimator_runtime(
    estimator: EstimatorConfig,
    profile: str,
) -> EstimatorRuntime:
    configured_path = _profile_path(estimator, profile)
    root = configured_estimator_source_root(configured_path)
    if root is None or not (root / "estimator" / "__init__.py").is_file():
        raise LocalProfileError(
            "estimator_path_invalid",
            f"{profile} estimator path must contain estimator/__init__.py.",
            profile=profile,
            path=str(root) if root else None,
        )

    sage_binary = _resolve_sage_binary(estimator.sage_binary)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(root), str(ROOT)))
    environment["PYTHONNOUSERSITE"] = "1"
    environment["EASYLATTICE_ESTIMATOR_ROOT"] = str(root)
    return EstimatorRuntime(
        sage_binary=sage_binary,
        root=root,
        environment=environment,
    )


def _last_output_line(*outputs: str) -> str | None:
    for output in outputs:
        lines = output.strip().splitlines()
        if lines:
            return lines[-1]
    return None


def run_origin_preflight(
    runtime: EstimatorRuntime,
    timeout_seconds: int | float,
) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [
                runtime.sage_binary,
                "-python",
                "-c",
                ESTIMATOR_ORIGIN_PREFLIGHT,
                str(runtime.root),
                str(ROOT),
            ],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=runtime.environment,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalProfileError(
            "estimator_preflight_timeout",
            f"Estimator validation timed out after {timeout_seconds}s.",
        ) from exc
    except OSError as exc:
        raise LocalProfileError(
            "estimator_preflight_failed",
            f"Could not start estimator validation: {type(exc).__name__}: {exc}",
        ) from exc

    if completed.returncode != 0:
        detail = _last_output_line(completed.stderr, completed.stdout)
        raise LocalProfileError(
            "estimator_preflight_failed",
            detail or f"Estimator validation exited with code {completed.returncode}.",
        )

    output = _last_output_line(completed.stdout)
    try:
        result = json.loads(output) if output is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        result = None
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise LocalProfileError(
            "estimator_preflight_failed",
            "Estimator validation returned invalid JSON output.",
        )
    if result["ok"] is True:
        return result

    message = result.get("message")
    if result.get("code") == "estimator_origin_mismatch" and isinstance(message, str):
        raise LocalProfileError("estimator_origin_mismatch", message)
    raise LocalProfileError(
        "estimator_preflight_failed",
        message if isinstance(message, str) else "Estimator validation failed.",
    )


def _git_warning(detail: str | None = None) -> GitMetadata:
    message = "Git metadata unavailable."
    if detail:
        message = f"{message} {detail}"
    return GitMetadata(commit=None, dirty=None, message=message)


def git_metadata(root: Path) -> GitMetadata:
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _git_warning(f"{type(exc).__name__}: {exc}")

    commit = revision.stdout.strip()
    if revision.returncode != 0 or not commit:
        return _git_warning(_last_output_line(revision.stderr, revision.stdout))

    try:
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitMetadata(
            commit=commit[:8],
            dirty=None,
            message=f"Git dirty state unavailable. {type(exc).__name__}: {exc}",
        )

    if status.returncode != 0:
        detail = _last_output_line(status.stderr, status.stdout)
        message = "Git dirty state unavailable."
        if detail:
            message = f"{message} {detail}"
        return GitMetadata(commit=commit[:8], dirty=None, message=message)
    return GitMetadata(commit=commit[:8], dirty=bool(status.stdout.strip()), message=None)


def _profile_result(
    *,
    available: bool,
    path: str | None,
    commit: str | None = None,
    dirty: bool | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> dict[str, object]:
    return {
        "available": available,
        "path": path,
        "commit": commit,
        "dirty": dirty,
        "error_code": error_code,
        "message": message,
    }


def profile_record(estimator: EstimatorConfig, profile: str) -> dict[str, object]:
    configured_path = _profile_path(estimator, profile)
    root = configured_estimator_source_root(configured_path)
    normalized_path = str(root) if root else None
    if not configured_path:
        return _profile_result(
            available=False,
            path=None,
            error_code="estimator_profile_not_configured",
            message=f"{profile} estimator path is not configured.",
        )

    try:
        runtime = prepare_estimator_runtime(estimator, profile)
        run_origin_preflight(runtime, estimator.default_timeout_seconds)
    except LocalProfileError as exc:
        return _profile_result(
            available=False,
            path=normalized_path,
            error_code=exc.code,
            message=exc.message,
        )

    metadata = git_metadata(runtime.root)
    return _profile_result(
        available=True,
        path=str(runtime.root),
        commit=metadata.commit,
        dirty=metadata.dirty,
        message=metadata.message,
    )


def local_profile_state(config: AppConfig | None = None) -> dict[str, object]:
    config = config or load_config()
    return {
        "ok": True,
        "sage_binary": config.estimator.sage_binary,
        "remote_configured": bool(config.estimator.remote_url),
        "profiles": {
            "standard": profile_record(config.estimator, "standard"),
            "enhanced": profile_record(config.estimator, "enhanced"),
        },
    }


def _raise_invalid_profile(record: dict[str, object], profile: str) -> None:
    if record.get("available") is True:
        return
    code = record.get("error_code")
    message = record.get("message")
    raise LocalProfileError(
        code if isinstance(code, str) else "estimator_preflight_failed",
        message if isinstance(message, str) else f"Could not validate {profile} estimator.",
        profile=profile,
    )


def _profile_config_path() -> Path:
    configured = os.environ.get("EASYLATTICE_CONFIG")
    return Path(configured).expanduser() if configured else ROOT / "config.local.json"


def _read_config_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = read_json(path)
    if not isinstance(value, dict):
        raise TypeError("Local configuration must contain a JSON object.")
    estimator = value.get("estimator")
    if estimator is not None and not isinstance(estimator, dict):
        raise TypeError("Local estimator configuration must contain a JSON object.")
    return value


def _atomic_write_config(path: Path, value: dict[str, Any]) -> None:
    temporary_path: Path | None = None
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
            temporary_path = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except Exception as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise LocalProfileError(
            "config_write_failed",
            f"Could not save local estimator configuration: {type(exc).__name__}: {exc}",
        ) from exc


def save_local_profile(payload: object) -> dict[str, object]:
    parsed = parse_profile_request(payload)
    estimator = EstimatorConfig(
        sage_binary=parsed.sage_binary,
        lattice_estimator_path=parsed.lattice_estimator_path,
        enhanced_lattice_estimator_path=parsed.enhanced_lattice_estimator_path,
    )
    _raise_invalid_profile(profile_record(estimator, "standard"), "standard")
    if parsed.enhanced_lattice_estimator_path is not None:
        _raise_invalid_profile(profile_record(estimator, "enhanced"), "enhanced")

    path = _profile_config_path()
    with _PROFILE_WRITE_LOCK:
        try:
            raw = _read_config_object(path)
            estimator_raw = raw.setdefault("estimator", {})
            estimator_raw.update(
                {
                    "sage_binary": parsed.sage_binary,
                    "lattice_estimator_path": parsed.lattice_estimator_path,
                    "enhanced_lattice_estimator_path": parsed.enhanced_lattice_estimator_path,
                }
            )
            _atomic_write_config(path, raw)
        except LocalProfileError:
            raise
        except Exception as exc:
            raise LocalProfileError(
                "config_write_failed",
                f"Could not save local estimator configuration: {type(exc).__name__}: {exc}",
            ) from exc
    return local_profile_state()


def _payload_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _normalized_variant(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def required_profile_for_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    nested = payload.get("request")
    request = nested if isinstance(nested, dict) else payload
    use_estimator = _payload_value(request, "use_estimator", "useEstimator")
    if use_estimator is None and request is not payload:
        use_estimator = _payload_value(payload, "use_estimator", "useEstimator")
    if not bool(use_estimator):
        return None

    problem = _normalized_variant(request.get("problem"))
    category = _normalized_variant(
        _payload_value(request, "hard_problem_category", "hardProblemCategory")
    )
    variant = _normalized_variant(
        _payload_value(request, "hard_problem_variant", "hardProblemVariant")
    )
    if variant in ENHANCED_VARIANTS:
        return "enhanced"
    if variant in STANDARD_VARIANTS:
        return "standard"
    if category == "ntru" or problem == "ntru":
        return "standard"
    if problem in ENHANCED_VARIANTS:
        return "enhanced"
    if problem in STANDARD_VARIANTS:
        return "standard"
    if category == "lwe" and not variant:
        return "standard"
    return None


def require_available_profile(
    payload: object,
    config: AppConfig | None = None,
) -> str | None:
    config = config or load_config()
    if config.estimator.remote_url:
        return None
    required_profile = required_profile_for_payload(payload)
    if required_profile is None:
        return None

    record = profile_record(config.estimator, required_profile)
    if record.get("available") is True:
        return required_profile
    profile_error_code = record.get("error_code")
    raise LocalProfileError(
        "estimator_profile_not_configured",
        f"The {required_profile} estimator profile is not available.",
        required_profile=required_profile,
        profile_error_code=(profile_error_code if isinstance(profile_error_code, str) else None),
    )
