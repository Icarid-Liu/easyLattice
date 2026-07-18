from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.estimator_contract import (
    ESTIMATOR_PROFILES as CONTRACT_ESTIMATOR_PROFILES,
    EstimatorRouteError,
    validate_estimator_route,
)
from app.json_safety import reject_json_constant, sanitize_json_value

RUNNER = ROOT / "app" / "estimator_runner.py"


def env_value(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default


DEFAULT_TIMEOUT_SECONDS = int(env_value("EASYLATTICE_ESTIMATOR_TIMEOUT_SECONDS", default="240"))
MAX_TIMEOUT_SECONDS = int(env_value("EASYLATTICE_ESTIMATOR_MAX_TIMEOUT_SECONDS", default="300"))
MAX_REQUEST_BYTES = int(env_value("EASYLATTICE_ESTIMATOR_MAX_REQUEST_BYTES", default=str(1_000_000)))
MAX_JOBS = int(env_value("EASYLATTICE_ESTIMATOR_MAX_JOBS", default="128"))
JOB_TTL_SECONDS = int(env_value("EASYLATTICE_ESTIMATOR_JOB_TTL_SECONDS", default="3600"))
WORKERS = max(1, min(4, int(env_value("EASYLATTICE_ESTIMATOR_WORKERS", default="1"))))
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in env_value("EASYLATTICE_ALLOWED_ORIGINS", default="*").split(",")
    if origin.strip()
]
ESTIMATOR_PROFILES = set(CONTRACT_ESTIMATOR_PROFILES)
INVALID_PROFILE_MESSAGE = "estimator_profile must be standard or enhanced."
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


@dataclass
class Job:
    id: str
    payload: dict[str, Any]
    timeout_seconds: int
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


jobs: dict[str, Job] = {}
lock = Lock()
executor = ThreadPoolExecutor(max_workers=WORKERS)


class EstimatorHandler(BaseHTTPRequestHandler):
    server_version = "easyLatticeEstimator/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.write_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in ("/", "/health"):
            self.write_json(
                {
                    "ok": True,
                    "service": "easylattice-estimator",
                    "default_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                    "max_timeout_seconds": MAX_TIMEOUT_SECONDS,
                    "workers": WORKERS,
                }
            )
            return

        if self.path.startswith("/jobs/"):
            job_id = self.path.removeprefix("/jobs/").strip("/")
            with lock:
                job = jobs.get(job_id)
            if not job:
                self.write_error(HTTPStatus.NOT_FOUND, "job not found")
                return
            self.write_json(job_to_json(job))
            return

        self.write_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        if self.path not in ("/jobs", "/estimate"):
            self.write_error(HTTPStatus.NOT_FOUND, "not found")
            return

        try:
            request = self.read_json()
            payload, timeout_seconds = parse_estimate_request(request)
        except EstimatorRouteError as exc:
            self.write_error(
                HTTPStatus.BAD_REQUEST,
                exc.message,
                code=exc.code,
            )
            return
        except ValueError as exc:
            self.write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except json.JSONDecodeError:
            self.write_error(HTTPStatus.BAD_REQUEST, "invalid JSON body")
            return

        cleanup_jobs()
        if self.path == "/estimate":
            job = create_job(payload, timeout_seconds)
            run_job(job)
            status = HTTPStatus.OK if job.status == "succeeded" else HTTPStatus.REQUEST_TIMEOUT
            self.write_json(job_to_json(job), status)
            return

        job = create_job(payload, timeout_seconds)
        submit_job(job)
        self.write_json(job_to_json(job), HTTPStatus.ACCEPTED)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BYTES:
            raise ValueError(f"request body is too large; max {MAX_REQUEST_BYTES} bytes")
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(
            body or "{}",
            parse_constant=reject_json_constant,
        )
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        safe_payload = sanitize_json_value(payload)
        data = json.dumps(
            safe_payload,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self.send_response(status)
        self.write_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        code: str | None = None,
    ) -> None:
        payload = {"ok": False, "error": message}
        if code is not None:
            payload["code"] = code
        self.write_json(payload, status)

    def write_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if "*" in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", "*")
        elif origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def parse_estimate_request(request: dict[str, Any]) -> tuple[dict[str, Any], int]:
    payload = request.get("payload", request)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    timeout_raw = request.get("timeout_seconds", request.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS))
    try:
        timeout_seconds = int(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer") from exc
    timeout_seconds = max(1, min(MAX_TIMEOUT_SECONDS, timeout_seconds))

    normalized = dict(payload)
    validate_payload(normalized)
    normalized["per_attack_timeout"] = clamp_per_attack_timeout(normalized, timeout_seconds)
    return normalized, timeout_seconds


def validate_payload(payload: dict[str, Any]) -> None:
    problem, _, _ = validate_estimator_route(
        payload.get("problem"),
        payload.get("estimator_profile"),
        payload.get("hard_problem_variant"),
    )

    n = int(payload.get("n", 0))
    q = int(payload.get("q", 0))
    if n < 1 or n > 16384:
        raise ValueError("n must be between 1 and 16384")
    if q < 2 or q.bit_length() > 64:
        raise ValueError("q must be at least 2 and at most 64 bits")

    if problem == "ntru":
        ntru_type = str(payload.get("ntru_type", "circulant"))
        if ntru_type not in {"circulant", "matrix"}:
            raise ValueError("ntru_type must be circulant or matrix")
        require_distribution(payload, "secret_distribution")
        require_distribution(payload, "error_distribution")
    elif "secret_distribution" in payload or "error_distribution" in payload:
        require_distribution(payload, "secret_distribution")
        require_distribution(payload, "error_distribution")
    else:
        require_distribution(payload, "distribution")


def require_distribution(payload: dict[str, Any], key: str) -> None:
    distribution = payload.get(key)
    if not isinstance(distribution, dict):
        raise ValueError(f"{key} must be a JSON object")
    estimator = distribution.get("estimator")
    if not isinstance(estimator, dict):
        raise ValueError(f"{key}.estimator must be a JSON object")
    estimator_type = estimator.get("type")
    allowed = {
        "centered_binomial",
        "sparse_ternary_fixed_weight",
        "discrete_gaussian",
        "uniform",
        "uniform_mod",
        "compression_noise",
        "composite_moment",
    }
    if estimator_type not in allowed:
        raise ValueError(f"unsupported estimator distribution type: {estimator_type}")


def clamp_per_attack_timeout(payload: dict[str, Any], timeout_seconds: int) -> int:
    default = 60 if payload.get("problem") == "ntru" else 30
    raw = payload.get("per_attack_timeout", default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, timeout_seconds, 90))


def create_job(payload: dict[str, Any], timeout_seconds: int) -> Job:
    job = Job(id=uuid.uuid4().hex, payload=payload, timeout_seconds=timeout_seconds)
    with lock:
        jobs[job.id] = job
    return job


def submit_job(job: Job) -> Future:
    return executor.submit(run_job, job)


def run_job(job: Job) -> None:
    with lock:
        job.status = "running"
        job.started_at = time.time()
    try:
        result = run_estimator_subprocess(job.payload, job.timeout_seconds)
        with lock:
            job.status = "succeeded" if result.get("ok") else "failed"
            job.result = result
            job.error = None if result.get("ok") else result.get("message", "estimator failed")
            job.finished_at = time.time()
    except subprocess.TimeoutExpired:
        with lock:
            job.status = "timeout"
            job.error = f"estimator timed out after {job.timeout_seconds}s"
            job.finished_at = time.time()
    except Exception as exc:
        with lock:
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()


def estimator_source_root(path: str) -> Path | None:
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = candidate.absolute()
    if (candidate / "estimator" / "__init__.py").is_file():
        return candidate
    if candidate.name == "estimator" and (candidate / "__init__.py").is_file():
        return candidate.parent
    return None


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


def run_estimator_subprocess(payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    sage_binary = os.environ.get("SAGE_BINARY", "sage")
    try:
        _, profile, _ = validate_estimator_route(
            payload.get("problem"),
            payload.get("estimator_profile"),
            payload.get("hard_problem_variant"),
        )
    except EstimatorRouteError as exc:
        return exc.as_result()

    path_name = (
        "ENHANCED_LATTICE_ESTIMATOR_PATH"
        if profile == "enhanced"
        else "LATTICE_ESTIMATOR_PATH"
    )
    configured_path = os.environ.get(path_name)
    if not configured_path:
        return {
            "ok": False,
            "code": f"{profile}_estimator_not_configured",
            "message": f"{profile} estimator path is not configured.",
        }
    estimator_path = estimator_source_root(configured_path)
    if estimator_path is None:
        return {
            "ok": False,
            "code": "estimator_path_invalid",
            "message": f"{profile} estimator path does not contain estimator/__init__.py.",
        }

    env = os.environ.copy()
    env["PYTHONPATH"] = str(estimator_path)
    env["PYTHONNOUSERSITE"] = "1"
    env["EASYLATTICE_ESTIMATOR_ROOT"] = str(estimator_path)

    preflight = subprocess.run(
        [
            sage_binary,
            "-python",
            "-c",
            ESTIMATOR_ORIGIN_PREFLIGHT,
            str(estimator_path),
            str(ROOT),
        ],
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
        env=env,
    )
    if preflight.returncode != 0:
        detail = (preflight.stderr or preflight.stdout).strip().splitlines()[-1:]
        message = detail[0] if detail else f"estimator preflight exited with code {preflight.returncode}"
        return {"ok": False, "code": "estimator_process_failed", "message": message}
    try:
        preflight_result = json.loads(preflight.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {
            "ok": False,
            "code": "estimator_process_failed",
            "message": "Estimator origin preflight returned invalid output.",
        }
    if not isinstance(preflight_result, dict):
        return {
            "ok": False,
            "code": "estimator_process_failed",
            "message": "Estimator origin preflight returned invalid output.",
        }
    if not preflight_result.get("ok"):
        return preflight_result

    completed = subprocess.run(
        [sage_binary, "-python", str(RUNNER)],
        input=json.dumps(payload, allow_nan=False),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        structured_error = decode_json_object(completed.stdout)
        if structured_error is not None and structured_error.get("ok") is False:
            return structured_error
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        message = detail[0] if detail else f"estimator exited with code {completed.returncode}"
        return {"ok": False, "message": message}

    try:
        result = json.loads(
            completed.stdout.strip().splitlines()[-1],
            parse_constant=reject_json_constant,
        )
        safe_result = sanitize_json_value(result)
        if not isinstance(safe_result, dict):
            raise ValueError("estimator result must be a JSON object")
        return safe_result
    except (json.JSONDecodeError, IndexError, ValueError) as exc:
        return {
            "ok": False,
            "message": f"estimator returned non-JSON output: {type(exc).__name__}",
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }


def cleanup_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with lock:
        expired = [
            job_id
            for job_id, job in jobs.items()
            if (job.finished_at or job.created_at) < cutoff
        ]
        for job_id in expired:
            jobs.pop(job_id, None)
        if len(jobs) <= MAX_JOBS:
            return
        removable = sorted(jobs.values(), key=lambda job: job.finished_at or job.created_at)
        for job in removable[: max(0, len(jobs) - MAX_JOBS)]:
            if job.status in {"queued", "running"}:
                continue
            jobs.pop(job.id, None)


def job_to_json(job: Job) -> dict[str, Any]:
    return {
        "ok": job.status == "succeeded",
        "job_id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "timeout_seconds": job.timeout_seconds,
        "result": job.result,
        "error": job.error,
    }


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "7860"))
    server = ThreadingHTTPServer((host, port), EstimatorHandler)
    print(f"easyLattice estimator worker listening on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
