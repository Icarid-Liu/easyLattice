from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import AppConfig, EstimatorConfig, configured_estimator_source_root
from .estimator_contract import EstimatorRouteError, validate_estimator_route
from .json_safety import reject_json_constant, sanitize_json_value
from .remote_estimator import estimate_remotely


STANDARD_LWE_VARIANTS = {"lwe", "lwr"}
ENHANCED_LWE_VARIANTS = {"rlwe", "mlwe", "rlwr", "mlwr"}
NTRU_VARIANTS = {"matrix", "ring"}

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
        )
    except EstimatorRouteError as exc:
        return exc.as_result()
    if config.estimator.remote_url:
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
    sage = shutil.which(config.sage_binary) or (
        config.sage_binary if Path(config.sage_binary).exists() else None
    )
    if not sage:
        return {
            "ok": False,
            "code": "sage_not_found",
            "message": f"Sage binary '{config.sage_binary}' not found.",
        }

    root = estimator_root(config, profile)
    if not root:
        return {
            "ok": False,
            "code": f"{profile}_estimator_not_configured",
            "message": f"{profile} estimator path is not configured.",
        }

    root_path = Path(root)
    if not (root_path / "estimator" / "__init__.py").is_file():
        return {
            "ok": False,
            "code": "estimator_path_invalid",
            "message": f"{profile} estimator path does not contain estimator/__init__.py.",
        }

    env = os.environ.copy()
    env["PYTHONPATH"] = str(root_path)
    env["PYTHONNOUSERSITE"] = "1"
    env["EASYLATTICE_ESTIMATOR_ROOT"] = str(root_path)
    runner = Path(__file__).with_name("estimator_runner.py")
    application_root = Path(__file__).resolve().parents[1]
    try:
        preflight = subprocess.run(
            [
                sage,
                "-python",
                "-c",
                ESTIMATOR_ORIGIN_PREFLIGHT,
                str(root_path),
                str(application_root),
            ],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        if preflight.returncode != 0:
            return process_failed(preflight)
        preflight_data = decode_json_object(preflight.stdout)
        if preflight_data is None:
            return {
                "ok": False,
                "code": "estimator_process_failed",
                "message": "Estimator origin preflight returned invalid output.",
            }
        if not preflight_data.get("ok"):
            return preflight_data

        completed = subprocess.run(
            [sage, "-python", str(runner)],
            input=json.dumps(payload, allow_nan=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
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
