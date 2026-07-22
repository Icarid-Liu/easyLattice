from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .config import AppConfig, EstimatorConfig, configured_estimator_source_root
from .estimator_contract import EstimatorRouteError, validate_estimator_route
from .job_progress import report_progress
from .json_safety import reject_json_constant, sanitize_json_value
from .local_profile import (
    ESTIMATOR_ORIGIN_PREFLIGHT,
    LocalProfileError,
    git_metadata,
    prepare_estimator_runtime,
    run_origin_preflight,
)
from .remote_estimator import estimate_remotely


STANDARD_LWE_VARIANTS = {"lwe", "lwr"}
ENHANCED_LWE_VARIANTS = {"rlwe", "mlwe", "rlwr", "mlwr"}
NTRU_VARIANTS = {"matrix", "ring"}


def estimator_profile_for(category: str, variant: str) -> str:
    if category == "ntru" and variant in NTRU_VARIANTS:
        return "standard"
    if category == "lwe" and variant in STANDARD_LWE_VARIANTS:
        return "standard"
    if category == "lwe" and variant in ENHANCED_LWE_VARIANTS:
        return "enhanced"
    raise ValueError(f"No estimator profile for {category}/{variant}.")


def estimator_root(config: EstimatorConfig, profile: str) -> str | None:
    if profile == "standard":
        configured = config.lattice_estimator_path
    elif profile == "enhanced":
        configured = config.enhanced_lattice_estimator_path
    else:
        raise ValueError("estimator profile must be standard or enhanced.")
    root = configured_estimator_source_root(configured)
    return str(root) if root else None


def run_estimator(
    payload: dict[str, Any],
    timeout: int,
    config: AppConfig,
    profile: str,
) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["estimator_profile"] = profile
    try:
        validate_estimator_route(
            normalized.get("problem"),
            normalized.get("estimator_profile"),
            normalized.get("hard_problem_variant"),
            normalized.get("ntru_type"),
        )
    except EstimatorRouteError as exc:
        return exc.as_result()
    if config.estimator.remote_url:
        report_progress("estimator_running", profile, None)
        return estimate_remotely(
            base_url=config.estimator.remote_url,
            payload=normalized,
            timeout_seconds=config.estimator.remote_timeout_seconds,
            poll_interval_seconds=config.estimator.remote_poll_interval_seconds,
        )
    return run_local_estimator(normalized, timeout, config.estimator, profile)


def run_local_estimator(
    payload: dict[str, Any],
    timeout: int,
    config: EstimatorConfig,
    profile: str,
) -> dict[str, Any]:
    try:
        runtime = prepare_estimator_runtime(config, profile)
    except LocalProfileError as exc:
        if exc.code == "estimator_path_invalid" and estimator_root(config, profile) is None:
            return {
                "ok": False,
                "code": f"{profile}_estimator_not_configured",
                "message": f"{profile} estimator path is not configured.",
            }
        return exc.as_result()

    metadata = git_metadata(runtime.root)
    report_progress("estimator_running", profile, metadata.commit)
    runner = Path(__file__).with_name("estimator_runner.py")
    try:
        preflight_data = run_origin_preflight(runtime, timeout)
        if not preflight_data.get("ok"):
            return preflight_data

        completed = subprocess.run(
            [runtime.sage_binary, "-python", str(runner)],
            input=json.dumps(payload, allow_nan=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=runtime.environment,
        )
    except LocalProfileError as exc:
        if exc.code == "estimator_preflight_timeout":
            return {
                "ok": False,
                "code": "estimator_timeout",
                "message": f"Estimator timed out after {timeout}s.",
            }
        if exc.code == "estimator_preflight_failed":
            return {
                "ok": False,
                "code": "estimator_process_failed",
                "message": exc.message,
            }
        return exc.as_result()
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "code": "estimator_timeout",
            "message": f"Estimator timed out after {timeout}s.",
        }
    except OSError as exc:
        return {
            "ok": False,
            "code": "estimator_process_failed",
            "message": f"Could not start estimator process: {type(exc).__name__}: {exc}",
        }

    if completed.returncode != 0:
        structured_error = decode_json_object(completed.stdout)
        if structured_error is not None and structured_error.get("ok") is False:
            return structured_error
        return process_failed(completed)

    data = decode_json_object(completed.stdout)
    if data is None:
        return {
            "ok": False,
            "code": "estimator_non_json",
            "message": "Estimator returned non-JSON output.",
        }
    return data


def decode_json_object(output: str) -> dict[str, Any] | None:
    try:
        data = json.loads(
            output.strip().splitlines()[-1],
            parse_constant=reject_json_constant,
        )
    except (json.JSONDecodeError, IndexError, ValueError):
        return None
    safe = sanitize_json_value(data)
    return safe if isinstance(safe, dict) else None


def process_failed(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
    return {
        "ok": False,
        "code": "estimator_process_failed",
        "message": detail[0] if detail else f"Estimator exited with code {completed.returncode}.",
    }
