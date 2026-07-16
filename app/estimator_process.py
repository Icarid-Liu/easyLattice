from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import AppConfig, EstimatorConfig
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
        return config.lattice_estimator_path
    if profile == "enhanced":
        return config.enhanced_lattice_estimator_path
    raise ValueError("estimator profile must be standard or enhanced.")


def run_estimator(
    payload: dict[str, Any],
    timeout: int,
    config: AppConfig,
    profile: str,
) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["estimator_profile"] = profile
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

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    expanded = str(Path(root).expanduser())
    env["PYTHONPATH"] = expanded if not existing else f"{expanded}{os.pathsep}{existing}"
    runner = Path(__file__).with_name("estimator_runner.py")
    try:
        completed = subprocess.run(
            [sage, "-python", str(runner)],
            input=json.dumps(payload),
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

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        return {
            "ok": False,
            "code": "estimator_process_failed",
            "message": detail[0] if detail else f"Estimator exited with code {completed.returncode}.",
        }

    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {
            "ok": False,
            "code": "estimator_non_json",
            "message": "Estimator returned non-JSON output.",
        }
