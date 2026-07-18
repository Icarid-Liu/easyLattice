from __future__ import annotations

import json
import mimetypes
import os
import socket
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
from .json_safety import reject_json_constant, sanitize_json_value


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "static"
API_WORKERS = max(1, min(4, int(os.environ.get("EASYLATTICE_API_WORKERS", "1"))))
MAX_API_JOBS = max(8, int(os.environ.get("EASYLATTICE_API_MAX_JOBS", "128")))
API_JOB_TTL_SECONDS = max(60, int(os.environ.get("EASYLATTICE_API_JOB_TTL_SECONDS", "3600")))
MAX_REQUEST_BODY_BYTES = 1_048_576
REQUEST_HEADER_READ_DEADLINE_SECONDS = 5.0
POST_BODY_READ_DEADLINE_SECONDS = 5.0
POST_BODY_READ_CHUNK_BYTES = 65_536
REQUEST_READ_CHUNK_BYTES = 8_192


class RequestBodyTooLarge(ValueError):
    pass


class RequestBodyReadTimeout(TimeoutError):
    pass


class RequestHeaderReadTimeout(Exception):
    pass


class DeadlineSocketReader:
    def __init__(self, connection: socket.socket):
        self.connection = connection
        self.buffer = bytearray()
        self.deadline: float | None = None
        self.timeout_type: type[Exception] = TimeoutError
        self.timeout_message = "Request read timed out."
        self.eof = False
        self.closed = False

    def begin_deadline(
        self,
        seconds: float,
        timeout_type: type[Exception],
        message: str,
    ) -> None:
        self.deadline = time.monotonic() + seconds
        self.timeout_type = timeout_type
        self.timeout_message = message

    def clear_deadline(self) -> None:
        self.deadline = None

    def readline(self, limit: int = -1) -> bytes:
        if self.closed or limit == 0:
            return b""
        while True:
            search_end = len(self.buffer) if limit < 0 else min(len(self.buffer), limit)
            newline = self.buffer.find(b"\n", 0, search_end)
            if newline >= 0:
                return self.consume(newline + 1)
            if limit >= 0 and len(self.buffer) >= limit:
                return self.consume(limit)
            if self.eof:
                return self.consume(search_end)
            read_size = REQUEST_READ_CHUNK_BYTES
            if limit >= 0:
                read_size = min(read_size, limit - len(self.buffer))
            chunk = self.recv(read_size)
            if not chunk:
                self.eof = True
            else:
                self.buffer.extend(chunk)

    def read_exact(self, size: int) -> bytes:
        if self.closed:
            return b""
        while len(self.buffer) < size and not self.eof:
            chunk = self.recv(
                min(size - len(self.buffer), POST_BODY_READ_CHUNK_BYTES)
            )
            if not chunk:
                self.eof = True
            else:
                self.buffer.extend(chunk)
        return self.consume(min(size, len(self.buffer)))

    def recv(self, size: int) -> bytes:
        if self.deadline is None:
            raise RuntimeError("Request read deadline is not active.")
        remaining_time = self.deadline - time.monotonic()
        if remaining_time <= 0:
            self.raise_timeout()
        previous_timeout = self.connection.gettimeout()
        read_timeout = (
            remaining_time
            if previous_timeout is None
            else min(previous_timeout, remaining_time)
        )
        try:
            self.connection.settimeout(read_timeout)
            chunk = self.connection.recv(size)
        except (socket.timeout, TimeoutError) as exc:
            raise self.timeout_type(self.timeout_message) from exc
        finally:
            self.connection.settimeout(previous_timeout)
        if time.monotonic() >= self.deadline:
            self.raise_timeout()
        return chunk

    def consume(self, size: int) -> bytes:
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def raise_timeout(self) -> None:
        raise self.timeout_type(self.timeout_message)

    def close(self) -> None:
        self.closed = True
        self.buffer.clear()


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
    rbufsize = 0

    def setup(self) -> None:
        super().setup()
        self.rfile.close()
        self.request_reader = DeadlineSocketReader(self.connection)
        self.rfile = self.request_reader

    def handle_one_request(self) -> None:
        self.requestline = ""
        self.request_version = self.default_request_version
        self.command = None
        self.request_reader.begin_deadline(
            REQUEST_HEADER_READ_DEADLINE_SECONDS,
            RequestHeaderReadTimeout,
            "Request headers read timed out.",
        )
        try:
            super().handle_one_request()
        except RequestHeaderReadTimeout as exc:
            self.request_reader.clear_deadline()
            self.write_preparse_timeout(str(exc))
        finally:
            self.request_reader.clear_deadline()

    def parse_request(self) -> bool:
        try:
            return super().parse_request()
        finally:
            self.request_reader.clear_deadline()

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
            except RequestBodyReadTimeout as exc:
                self.write_request_timeout(str(exc))
                return
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
            except RequestBodyReadTimeout as exc:
                self.write_request_timeout(str(exc))
                return
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
        except RequestBodyReadTimeout as exc:
            self.write_request_timeout(str(exc))
            return
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
        body_bytes = self.read_request_body(length)
        try:
            body = body_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Request body must be valid UTF-8.") from exc
        numeric_options: dict[str, Any] = {
            "parse_constant": reject_json_constant,
        }
        if preserve_numeric_lexemes:
            numeric_options.update({"parse_int": str, "parse_float": str})
        payload = json.loads(body or "{}", **numeric_options)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def read_request_body(self, length: int) -> bytes:
        self.request_reader.begin_deadline(
            POST_BODY_READ_DEADLINE_SECONDS,
            RequestBodyReadTimeout,
            "Request body read timed out.",
        )
        try:
            body = self.request_reader.read_exact(length)
        finally:
            self.request_reader.clear_deadline()
        if len(body) != length:
            raise ValueError("Request body ended before Content-Length bytes were received.")
        return body

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

    def write_request_timeout(self, message: str) -> None:
        self.close_connection = True
        try:
            self.write_error(HTTPStatus.REQUEST_TIMEOUT, message)
        except OSError:
            pass

    def write_preparse_timeout(self, message: str) -> None:
        self.close_connection = True
        data = json.dumps(
            sanitize_json_value({"ok": False, "error": message}),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        headers = [
            "HTTP/1.1 408 Request Timeout",
            f"Server: {self.version_string()}",
            f"Date: {self.date_time_string()}",
            "Content-Type: application/json; charset=utf-8",
            f"Content-Length: {len(data)}",
            "Connection: close",
        ]
        if "*" in allowed_origins():
            headers.extend((
                "Access-Control-Allow-Origin: *",
                "Access-Control-Allow-Methods: GET, POST, OPTIONS",
                "Access-Control-Allow-Headers: Content-Type",
                "Access-Control-Max-Age: 86400",
            ))
        response = ("\r\n".join(headers) + "\r\n\r\n").encode("latin-1") + data
        try:
            self.wfile.write(response)
            self.wfile.flush()
        except OSError:
            pass

    def write_cors_headers(self) -> None:
        allowed = allowed_origins()
        if not allowed:
            return
        request_headers = getattr(self, "headers", None)
        request_origin = request_headers.get("Origin", "") if request_headers else ""
        origin = cors_origin_for(request_origin, allowed)
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
