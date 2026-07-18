from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from .agent import recommend_with_agent
from .config import public_config
from .decryption_failure import calculate_decryption_failure
from .json_safety import sanitize_json_value


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "static"
API_WORKERS = max(1, min(4, int(os.environ.get("EASYLATTICE_API_WORKERS", "1"))))
MAX_API_JOBS = max(8, int(os.environ.get("EASYLATTICE_API_MAX_JOBS", "128")))
API_JOB_TTL_SECONDS = max(60, int(os.environ.get("EASYLATTICE_API_JOB_TTL_SECONDS", "3600")))
MAX_REQUEST_BODY_BYTES = 1_048_576


class RequestBodyTooLarge(ValueError):
    pass


@dataclass
class RecommendationJob:
    id: str
    payload: dict[str, Any]
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


jobs: dict[str, RecommendationJob] = {}
jobs_lock = Lock()
executor = ThreadPoolExecutor(max_workers=API_WORKERS)


def allowed_origins() -> list[str]:
    raw = os.environ.get("EASYLATTICE_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def cors_origin_for(origin: str, allowed: list[str]) -> str | None:
    if "*" in allowed:
        return "*"
    if origin and origin in allowed:
        return origin
    return None


def create_job(payload: dict[str, Any]) -> RecommendationJob:
    job = RecommendationJob(id=uuid.uuid4().hex, payload=payload)
    with jobs_lock:
        jobs[job.id] = job
    return job


def submit_job(job: RecommendationJob) -> Future:
    return executor.submit(run_job, job)


def run_job(job: RecommendationJob) -> None:
    with jobs_lock:
        job.status = "running"
        job.started_at = time.time()
    try:
        result = recommend_with_agent(job.payload)
        with jobs_lock:
            job.status = "succeeded"
            job.result = result
            job.error = None
            job.finished_at = time.time()
    except Exception as exc:
        with jobs_lock:
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()


def cleanup_jobs() -> None:
    cutoff = time.time() - API_JOB_TTL_SECONDS
    with jobs_lock:
        expired = [
            job_id
            for job_id, job in jobs.items()
            if job.created_at < cutoff and job.status in {"succeeded", "failed"}
        ]
        for job_id in expired:
            jobs.pop(job_id, None)


def job_to_json(job: RecommendationJob) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": job.status != "failed",
        "job_id": job.id,
        "status": job.status,
        "created_at": round(job.created_at, 3),
        "started_at": round(job.started_at, 3) if job.started_at else None,
        "finished_at": round(job.finished_at, 3) if job.finished_at else None,
    }
    if job.result is not None:
        payload["result"] = job.result
    if job.error is not None:
        payload["error"] = job.error
    return payload


class EasyLatticeHandler(BaseHTTPRequestHandler):
    server_version = "easyLattice/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.write_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self.serve_file(STATIC_ROOT / "index.html")
            return
        if parsed.path in ("/app-model.js", "/app.js", "/preview-data.js", "/styles.css"):
            self.serve_file(STATIC_ROOT / parsed.path.lstrip("/"))
            return
        if parsed.path.startswith("/static/"):
            relative = parsed.path.removeprefix("/static/")
            self.serve_file(STATIC_ROOT / relative)
            return
        if parsed.path == "/api/health":
            self.write_json({"ok": True})
            return
        if parsed.path == "/api/config/public":
            self.write_json(public_config())
            return
        if parsed.path.startswith("/api/agent/jobs/"):
            job_id = parsed.path.removeprefix("/api/agent/jobs/").strip("/")
            with jobs_lock:
                job = jobs.get(job_id)
            if not job:
                self.write_error(HTTPStatus.NOT_FOUND, "job not found")
                return
            self.write_json(job_to_json(job))
            return
        self.write_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/decryption-failure/calculate":
            try:
                payload = self.read_json(preserve_numeric_lexemes=True)
                result = calculate_decryption_failure(payload)
            except RequestBodyTooLarge as exc:
                self.write_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(exc))
                return
            except ValueError as exc:
                self.write_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except json.JSONDecodeError:
                self.write_error(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return
            except Exception as exc:
                self.write_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")
                return

            self.write_json(result)
            return

        if parsed.path == "/api/agent/jobs":
            try:
                payload = self.read_json()
            except RequestBodyTooLarge as exc:
                self.write_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(exc))
                return
            except ValueError as exc:
                self.write_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except json.JSONDecodeError:
                self.write_error(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return

            cleanup_jobs()
            with jobs_lock:
                job_count = len(jobs)
            if job_count >= MAX_API_JOBS:
                self.write_error(HTTPStatus.TOO_MANY_REQUESTS, "too many queued estimator jobs")
                return

            job = create_job(payload)
            submit_job(job)
            self.write_json(job_to_json(job), HTTPStatus.ACCEPTED)
            return

        if parsed.path not in ("/api/rlwe/recommend", "/api/agent/recommend"):
            self.write_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        try:
            payload = self.read_json()
            result = recommend_with_agent(payload)
        except RequestBodyTooLarge as exc:
            self.write_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(exc))
            return
        except ValueError as exc:
            self.write_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except json.JSONDecodeError:
            self.write_error(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
            return
        except Exception as exc:
            self.write_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")
            return

        self.write_json(result)

    def read_json(self, *, preserve_numeric_lexemes: bool = False) -> dict[str, Any]:
        length = self.request_content_length()
        try:
            body = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Request body must be valid UTF-8.") from exc
        numeric_options = (
            {
                "parse_int": str,
                "parse_float": str,
                "parse_constant": str,
            }
            if preserve_numeric_lexemes
            else {}
        )
        payload = json.loads(body or "{}", **numeric_options)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def request_content_length(self) -> int:
        values = self.headers.get_all("Content-Length", [])
        if not values:
            raise ValueError("Content-Length header is required.")
        if len(values) != 1:
            raise ValueError("Content-Length header must appear exactly once.")
        raw = values[0]
        if not raw or not raw.isascii() or not raw.isdigit():
            raise ValueError("Content-Length must be a positive decimal integer.")
        normalized = raw.lstrip("0") or "0"
        maximum = str(MAX_REQUEST_BODY_BYTES)
        if len(normalized) > len(maximum) or (
            len(normalized) == len(maximum) and normalized > maximum
        ):
            raise RequestBodyTooLarge(
                f"Content-Length exceeds {MAX_REQUEST_BODY_BYTES} "
                "(MAX_REQUEST_BODY_BYTES)."
            )
        length = int(normalized)
        if length < 1:
            raise ValueError("Content-Length must be a positive decimal integer.")
        return length

    def serve_file(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(STATIC_ROOT.resolve()) or not resolved.is_file():
            self.write_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        content_type = (
            "text/javascript; charset=utf-8"
            if resolved.suffix == ".js"
            else mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        )
        data = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.write_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(
            sanitize_json_value(payload),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self.send_response(status)
        self.write_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_error(self, status: HTTPStatus, message: str) -> None:
        self.write_json({"ok": False, "error": message}, status)

    def write_cors_headers(self) -> None:
        allowed = allowed_origins()
        if not allowed:
            return
        origin = cors_origin_for(self.headers.get("Origin", ""), allowed)
        if not origin:
            return
        self.send_header("Access-Control-Allow-Origin", origin)
        if origin != "*":
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def run() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), EasyLatticeHandler)
    print(f"easyLattice listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
