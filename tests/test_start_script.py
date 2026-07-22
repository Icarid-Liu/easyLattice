from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = ROOT / "start.sh"


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class StartScriptTest(unittest.TestCase):
    def test_help_documents_supported_options_without_starting_server(self) -> None:
        result = subprocess.run(
            [str(START_SCRIPT), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--no-open", "--host", "--port", "--force", "--with-estimator"):
            self.assertIn(option, result.stdout)
        self.assertNotIn("easyLattice listening", result.stdout)

    def test_no_open_starts_foreground_server_with_temporary_config(self) -> None:
        port = free_loopback_port()
        opener = build_opener(ProxyHandler({}))

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.local.json"
            environment = os.environ.copy()
            environment.pop("HOST", None)
            environment.pop("PORT", None)
            environment["EASYLATTICE_CONFIG"] = str(config_path)

            with tempfile.TemporaryFile(mode="w+b") as output:
                process = subprocess.Popen(
                    [
                        str(START_SCRIPT),
                        "--no-open",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    cwd=ROOT,
                    env=environment,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    deadline = time.monotonic() + 20
                    health_url = f"http://127.0.0.1:{port}/api/health"
                    payload = None
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            self.fail(self._process_output(process, output))
                        try:
                            with opener.open(health_url, timeout=1) as response:
                                payload = json.load(response)
                            break
                        except (OSError, URLError, json.JSONDecodeError):
                            time.sleep(0.1)

                    if payload != {"ok": True}:
                        self.fail(self._process_output(process, output))
                    self.assertTrue(config_path.is_file())
                    self.assertIsNone(process.poll(), "start.sh did not retain the foreground server")
                finally:
                    self._terminate_process_group(process)

    @staticmethod
    def _process_output(process: subprocess.Popen[bytes], output) -> str:
        output.flush()
        output.seek(0)
        text = output.read().decode("utf-8", errors="replace")
        return f"process exited with {process.poll()}; output:\n{text}"

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.poll() is not None:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
