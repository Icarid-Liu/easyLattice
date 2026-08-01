from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from threading import Event
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
    timeout: int | float | None,
    config: AppConfig,
    profile: str,
    cancel_event: Event | None = None,
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
        if cancel_event is not None and cancel_event.is_set():
            return cancellation_result()
        report_progress("estimator_running", profile, None)
        return estimate_remotely(
            base_url=config.estimator.remote_url,
            payload=normalized,
            timeout_seconds=config.estimator.remote_timeout_seconds,
            poll_interval_seconds=config.estimator.remote_poll_interval_seconds,
        )
    return run_local_estimator(
        normalized,
        None,
        config.estimator,
        profile,
        cancel_event=cancel_event,
    )


def run_local_estimator(
    payload: dict[str, Any],
    timeout: int | float | None,
    config: EstimatorConfig,
    profile: str,
    cancel_event: Event | None = None,
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

        if cancel_event is not None and cancel_event.is_set():
            return cancellation_result()

        command = [runtime.sage_binary, "-python", str(runner)]
        input_data = json.dumps(payload, allow_nan=False)
        if cancel_event is None:
            # Preserve the simple path (and its stable CompletedProcess
            # contract) for existing callers.  Jobs that opt into task
            # cancellation use the polled Popen path below.
            completed = subprocess.run(
                command,
                input=input_data,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=runtime.environment,
            )
        else:
            completed = run_cancellable_process(
                command,
                input_data,
                timeout=timeout,
                environment=runtime.environment,
                cancel_event=cancel_event,
            )
            if isinstance(completed, dict):
                return completed
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


def cancellation_result() -> dict[str, Any]:
    return {
        "ok": False,
        "code": "attack_cancelled",
        "message": "Estimator attack was cancelled.",
        "cancelled": True,
    }


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Terminate a Sage process and its descendants when possible."""

    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass


def run_cancellable_process(
    command: list[str],
    input_data: str,
    *,
    timeout: int | float | None,
    environment: dict[str, str],
    cancel_event: Event,
) -> subprocess.CompletedProcess[str] | dict[str, Any]:
    """Run Sage while polling cancellation and the optional wall timeout."""

    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            start_new_session=(os.name == "posix"),
        )
        # Send the request once; communicate() below drains both pipes while
        # it waits, avoiding the deadlock risk of a direct stdout.read().
        assert process.stdin is not None
        process.stdin.write(input_data)
        process.stdin.close()
        # communicate() attempts to flush ``self.stdin`` before draining the
        # pipes.  The request has already been sent, so mark it unavailable
        # to avoid flushing a closed handle on the polling path.
        process.stdin = None
    except OSError as exc:
        raise exc

    while True:
        if cancel_event.is_set():
            _terminate_process(process)
            process.communicate()
            return cancellation_result()
        if timeout is not None and time.monotonic() - started >= timeout:
            _terminate_process(process)
            process.communicate()
            raise subprocess.TimeoutExpired(command, timeout)
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            return subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        except subprocess.TimeoutExpired:
            continue


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
