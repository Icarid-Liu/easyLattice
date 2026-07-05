from __future__ import annotations

import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import public_config
from .parameter_search import recommend_rlwe


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "static"


class AILatticeHandler(BaseHTTPRequestHandler):
    server_version = "AILattice/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self.serve_file(STATIC_ROOT / "index.html")
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
        self.write_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/rlwe/recommend":
            self.write_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body or "{}")
            result = recommend_rlwe(payload)
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

    def serve_file(self, path: Path) -> None:
        resolved = path.resolve()
        if not str(resolved).startswith(str(STATIC_ROOT.resolve())) or not resolved.is_file():
            self.write_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        data = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_error(self, status: HTTPStatus, message: str) -> None:
        self.write_json({"ok": False, "error": message}, status)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def run() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), AILatticeHandler)
    print(f"AILattice listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
