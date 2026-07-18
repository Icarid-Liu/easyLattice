from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from .json_safety import reject_json_constant, sanitize_json_value


MAX_REMOTE_RESPONSE_BYTES = 2_000_000


@dataclass(frozen=True)
class RemoteEstimatorConfig:
    base_url: str
    timeout_seconds: int
    poll_interval_seconds: float = 2.0
    request_timeout_seconds: int = 20


class RemoteEstimatorClient:
    def __init__(self, config: RemoteEstimatorConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/") + "/"

    def estimate(self, payload: dict[str, Any]) -> dict[str, Any]:
        submitted = self.safe_object(
            self.post_json(
                "jobs",
                {
                    "timeout_seconds": self.config.timeout_seconds,
                    "payload": payload,
                },
            )
        )
        job_id = submitted.get("job_id")
        if not job_id:
            return {
                "ok": False,
                "message": "Remote estimator did not return a job id.",
                "raw": submitted,
            }

        deadline = time.monotonic() + self.config.timeout_seconds + self.config.request_timeout_seconds
        while time.monotonic() < deadline:
            job = self.safe_object(self.get_json(f"jobs/{job_id}"))
            status = str(job.get("status", "unknown"))
            if status == "succeeded":
                result = job.get("result")
                if isinstance(result, dict):
                    result = self.safe_object(result)
                    result.setdefault("remote_job_id", job_id)
                    return result
                return {
                    "ok": False,
                    "message": "Remote estimator job succeeded without a JSON result.",
                    "remote_job_id": job_id,
                    "raw": job,
                }
            if status in {"failed", "timeout"}:
                result = job.get("result")
                if isinstance(result, dict):
                    result = self.safe_object(result)
                    result.setdefault("remote_job_id", job_id)
                    return result
                return {
                    "ok": False,
                    "message": job.get("error") or f"Remote estimator job {status}.",
                    "remote_job_id": job_id,
                    "raw": job,
                }
            time.sleep(self.config.poll_interval_seconds)

        return {
            "ok": False,
            "message": f"Remote estimator polling timed out after {self.config.timeout_seconds}s.",
            "remote_job_id": job_id,
        }

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            urljoin(self.base_url, path),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self.open_json(request)

    def get_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(urljoin(self.base_url, path), method="GET")
        return self.open_json(request)

    def open_json(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                raw = response.read(MAX_REMOTE_RESPONSE_BYTES + 1)
                if len(raw) > MAX_REMOTE_RESPONSE_BYTES:
                    raise RuntimeError("Remote estimator response is too large.")
                body = raw.decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_REMOTE_RESPONSE_BYTES).decode("utf-8", errors="replace")
            try:
                data = json.loads(body, parse_constant=reject_json_constant)
            except (json.JSONDecodeError, ValueError):
                data = {"error": body}
            message = data.get("error") if isinstance(data, dict) else body
            raise RuntimeError(f"Remote estimator HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Remote estimator request failed: {exc.reason}") from exc

        try:
            data = json.loads(body, parse_constant=reject_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Remote estimator returned non-JSON output.") from exc
        safe = sanitize_json_value(data)
        if not isinstance(safe, dict):
            raise RuntimeError("Remote estimator response must be a JSON object.")
        return safe

    @staticmethod
    def safe_object(value: Any) -> dict[str, Any]:
        safe = sanitize_json_value(value)
        if not isinstance(safe, dict):
            raise RuntimeError("Remote estimator response must be a JSON object.")
        return safe


def estimate_remotely(
    base_url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    client = RemoteEstimatorClient(
        RemoteEstimatorConfig(
            base_url=base_url,
            timeout_seconds=max(1, min(300, int(timeout_seconds))),
            poll_interval_seconds=poll_interval_seconds,
        )
    )
    try:
        return client.estimate(payload)
    except Exception as exc:
        return {
            "ok": False,
            "message": f"{type(exc).__name__}: {exc}",
        }
