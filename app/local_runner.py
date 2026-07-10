from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import secrets
import shutil
import sys
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunsplit

from .agent import recommend_with_agent
from .config import AppConfig, EstimatorConfig, LLMConfig, ScriptsConfig, public_config
from .decryption_failure import calculate_decryption_failure


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "static"
DEFAULT_PUBLIC_URL = "https://icarid-liu.github.io/easyLattice/static/index.html"
RUNNER_TOKEN_HEADER = "X-EasyLattice-Runner-Token"


@dataclass
class RunnerJob:
    id: str
    payload: dict[str, Any]
    config: AppConfig
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class LocalRunnerState:
    public_url: str = DEFAULT_PUBLIC_URL
    allowed_origins: set[str] = field(default_factory=set)
    sage_binary: str | None = None
    lattice_estimator_path: str | None = None
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    jobs: dict[str, RunnerJob] = field(default_factory=dict)
    jobs_lock: Lock = field(default_factory=Lock)
    executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=1))

    def __post_init__(self) -> None:
        self.public_url = self.public_url.strip() or DEFAULT_PUBLIC_URL
        self.allowed_origins.add(public_origin(self.public_url))
        self.sage_binary = (
            validate_sage_binary(self.sage_binary) if self.sage_binary else discover_sage_binary()
        )
        self.lattice_estimator_path = (
            validate_estimator_root(self.lattice_estimator_path)
            if self.lattice_estimator_path
            else discover_estimator_root()
        )

    def configuration(self) -> AppConfig:
        return AppConfig(
            estimator=EstimatorConfig(
                sage_binary=self.sage_binary or "sage",
                lattice_estimator_path=self.lattice_estimator_path,
                default_timeout_seconds=16,
                per_attack_timeout_seconds=12,
                remote_url=None,
                remote_timeout_seconds=240,
                remote_poll_interval_seconds=2.0,
            ),
            llm=LLMConfig(enabled=False),
            scripts=ScriptsConfig(),
            source="local-runner",
        )

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "runner": True,
            "configured": bool(self.sage_binary and self.lattice_estimator_path),
            "sage": {"path": self.sage_binary, "configured": bool(self.sage_binary)},
            "estimator": {
                "path": self.lattice_estimator_path,
                "configured": bool(self.lattice_estimator_path),
            },
            "public_url": self.public_url,
        }

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "sageBinary" in payload or "sage_binary" in payload:
            raw_sage = payload.get("sageBinary", payload.get("sage_binary"))
            self.sage_binary = validate_sage_binary(raw_sage)
        if "latticeEstimatorPath" in payload or "lattice_estimator_path" in payload:
            raw_estimator = payload.get("latticeEstimatorPath", payload.get("lattice_estimator_path"))
            self.lattice_estimator_path = validate_estimator_root(raw_estimator)
        return self.status()

    def submit_job(self, payload: dict[str, Any]) -> RunnerJob:
        job = RunnerJob(id=uuid.uuid4().hex, payload=payload, config=self.configuration())
        with self.jobs_lock:
            self.jobs[job.id] = job
        self.executor.submit(self.run_job, job)
        return job

    def run_job(self, job: RunnerJob) -> None:
        with self.jobs_lock:
            job.status = "running"
            job.started_at = time.time()
        try:
            result = recommend_with_agent(job.payload, config=job.config)
            with self.jobs_lock:
                job.status = "succeeded"
                job.result = result
                job.finished_at = time.time()
        except Exception as exc:
            with self.jobs_lock:
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.finished_at = time.time()

    def job_payload(self, job_id: str) -> dict[str, Any] | None:
        with self.jobs_lock:
            job = self.jobs.get(job_id)
        if job is None:
            return None
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

    def is_allowed_origin(self, origin: str) -> bool:
        if not origin:
            return True
        if origin in self.allowed_origins:
            return True
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


def create_runner_server(state: LocalRunnerState, port: int = 0) -> ThreadingHTTPServer:
    handler = local_runner_handler(state)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def local_runner_handler(state: LocalRunnerState):
    class LocalRunnerHandler(BaseHTTPRequestHandler):
        server_version = "easyLattice-local-runner/0.1"

        def do_OPTIONS(self) -> None:
            if not state.is_allowed_origin(self.headers.get("Origin", "")):
                self.write_error(HTTPStatus.FORBIDDEN, "origin is not allowed")
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.write_cors_headers()
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                self.write_json({"ok": True, "runner": True})
                return
            if parsed.path == "/api/runner/status":
                if self.require_token():
                    self.write_json(state.status())
                return
            if parsed.path == "/api/config/public":
                if self.require_token():
                    self.write_json(public_config(state.configuration()))
                return
            if parsed.path.startswith("/api/agent/jobs/"):
                if not self.require_token():
                    return
                job = state.job_payload(parsed.path.removeprefix("/api/agent/jobs/").strip("/"))
                if job is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "job not found")
                    return
                self.write_json(job)
                return
            self.serve_static(parsed.path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not self.require_token():
                return
            try:
                payload = self.read_json()
                if parsed.path == "/api/runner/configure":
                    self.write_json(state.configure(payload))
                    return
                if parsed.path == "/api/agent/recommend":
                    self.write_json(recommend_with_agent(payload, config=state.configuration()))
                    return
                if parsed.path == "/api/agent/jobs":
                    self.write_json(state.job_payload(state.submit_job(payload).id) or {}, HTTPStatus.ACCEPTED)
                    return
                if parsed.path == "/api/decryption-failure/calculate":
                    self.write_json(calculate_decryption_failure(payload))
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
            self.write_error(HTTPStatus.NOT_FOUND, "Not found")

        def require_token(self) -> bool:
            token = self.headers.get(RUNNER_TOKEN_HEADER, "")
            if hmac.compare_digest(token, state.token):
                return True
            self.write_error(HTTPStatus.UNAUTHORIZED, "local runner token is required")
            return False

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object")
            return payload

        def serve_static(self, path: str) -> None:
            if path in {"/", "/index.html"}:
                candidate = STATIC_ROOT / "index.html"
            elif path in {"/app.js", "/styles.css"}:
                candidate = STATIC_ROOT / path.lstrip("/")
            elif path.startswith("/static/"):
                candidate = STATIC_ROOT / path.removeprefix("/static/")
            else:
                self.write_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            resolved = candidate.resolve()
            if not str(resolved).startswith(str(STATIC_ROOT.resolve())) or not resolved.is_file():
                self.write_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            data = resolved.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.write_cors_headers()
            self.send_header("Content-Type", mimetypes.guess_type(resolved.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.write_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def write_error(self, status: HTTPStatus, message: str) -> None:
            self.write_json({"ok": False, "error": message}, status)

        def write_cors_headers(self) -> None:
            origin = self.headers.get("Origin", "")
            if not origin or not state.is_allowed_origin(origin):
                return
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", f"Content-Type, {RUNNER_TOKEN_HEADER}")
            self.send_header("Access-Control-Max-Age", "86400")

        def log_message(self, fmt: str, *args) -> None:
            print(f"{self.address_string()} - {fmt % args}")

    return LocalRunnerHandler


def validate_sage_binary(raw: Any) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    direct = Path(value).expanduser()
    if direct.is_file() and os.access(direct, os.X_OK):
        return str(direct.resolve())
    resolved = shutil.which(value)
    if resolved:
        return str(Path(resolved).resolve())
    raise ValueError("Sage path must resolve to an executable file.")


def validate_estimator_root(raw: Any) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    root = path.parent if path.name == "estimator" else path
    if not (root / "estimator" / "__init__.py").is_file():
        raise ValueError("Estimator path must contain estimator/__init__.py.")
    return str(root)


def discover_sage_binary() -> str | None:
    for candidate in (os.environ.get("SAGE_BINARY"), shutil.which("sage"), shutil.which("sage.exe")):
        try:
            value = validate_sage_binary(candidate)
        except ValueError:
            continue
        if value:
            return value
    return None


def discover_estimator_root() -> str | None:
    candidates = [
        os.environ.get("LATTICE_ESTIMATOR_PATH"),
        ROOT / ".external" / "lattice-estimator",
        ROOT / "lattice-estimator",
        ROOT.parent / "lattice-estimator",
        Path.home() / "lattice-estimator",
        Path("/opt/lattice-estimator"),
    ]
    for candidate in candidates:
        try:
            value = validate_estimator_root(candidate)
        except ValueError:
            continue
        if value:
            return value
    return None


def runner_page_url(public_url: str, port: int, token: str) -> str:
    parsed = urlparse(public_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["apiBase"] = f"http://127.0.0.1:{port}"
    query["runnerToken"] = token
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def public_origin(public_url: str) -> str:
    parsed = urlparse(public_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("public URL must include scheme and host.")
    return f"{parsed.scheme}://{parsed.netloc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="easyLattice localhost runner")
    parser.add_argument("--port", type=int, default=0, help="loopback port; 0 chooses a free port")
    parser.add_argument("--public-url", default=os.environ.get("EASYLATTICE_PUBLIC_URL", DEFAULT_PUBLIC_URL))
    parser.add_argument("--allow-origin", action="append", default=[])
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    state = LocalRunnerState(public_url=args.public_url, allowed_origins=set(args.allow_origin))
    server = create_runner_server(state, port=max(0, args.port))
    port = int(server.server_address[1])
    page_url = runner_page_url(state.public_url, port, state.token)
    print(f"easyLattice local runner listening on http://127.0.0.1:{port}")
    print(f"Open: {page_url}")
    if not args.no_browser:
        webbrowser.open(page_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
