from __future__ import annotations

import json
import os
import shutil
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
SETUP_SCRIPT = ROOT / "scripts" / "setup-local.sh"


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def initialize_estimator_fixture(path: Path) -> None:
    package = path / "estimator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=easyLattice Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=path,
        check=True,
        capture_output=True,
    )


def isolated_checkout(destination: Path) -> Path:
    checkout = destination / "easy lattice checkout"
    shutil.copytree(
        ROOT,
        checkout,
        ignore=shutil.ignore_patterns(
            ".git",
            ".external",
            ".worktrees",
            "__pycache__",
            "*.pyc",
            "config.local.json",
        ),
    )
    return checkout


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

    def test_setup_supplements_existing_empty_paths_without_force(self) -> None:
        with tempfile.TemporaryDirectory(prefix="easy lattice setup ") as directory:
            root = Path(directory)
            standard = root / "standard estimator"
            enhanced = root / "enhanced estimator"
            for source in (standard, enhanced):
                package = source / "estimator"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text("", encoding="utf-8")
            config_path = root / "existing config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "estimator": {
                            "sage_binary": "/Applications/Existing Sage.app/sage",
                            "lattice_estimator_path": None,
                            "enhanced_lattice_estimator_path": "",
                            "remote_timeout_seconds": 77,
                        },
                        "unrelated": "keep",
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "EASYLATTICE_CONFIG": str(config_path),
                    "SAGE_BINARY": "/Applications/New Sage.app/sage",
                    "LATTICE_ESTIMATOR_PATH": str(standard),
                    "ENHANCED_LATTICE_ESTIMATOR_PATH": str(enhanced),
                }
            )

            result = subprocess.run(
                [str(SETUP_SCRIPT)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            saved["estimator"]["sage_binary"],
            "/Applications/Existing Sage.app/sage",
        )
        self.assertEqual(
            saved["estimator"]["lattice_estimator_path"],
            str(standard.resolve()),
        )
        self.assertEqual(
            saved["estimator"]["enhanced_lattice_estimator_path"],
            str(enhanced.resolve()),
        )
        self.assertEqual(saved["estimator"]["remote_timeout_seconds"], 77)
        self.assertEqual(saved["unrelated"], "keep")

    def test_with_estimator_clones_local_fixtures_in_checkout_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="easy lattice clone ") as directory:
            root = Path(directory)
            checkout = isolated_checkout(root)
            standard_fixture = root / "standard fixture"
            enhanced_fixture = root / "enhanced fixture"
            initialize_estimator_fixture(standard_fixture)
            initialize_estimator_fixture(enhanced_fixture)
            config_path = checkout / "config.local.json"
            sage_path = "/Applications/SageMath 10.7.app/Contents/MacOS/sage"
            environment = os.environ.copy()
            environment.update(
                {
                    "EASYLATTICE_CONFIG": str(config_path),
                    "SAGE_BINARY": sage_path,
                    "EASYLATTICE_STANDARD_ESTIMATOR_REPOSITORY": (
                        standard_fixture.as_uri()
                    ),
                    "EASYLATTICE_ENHANCED_ESTIMATOR_REPOSITORY": (
                        enhanced_fixture.as_uri()
                    ),
                }
            )

            result = subprocess.run(
                [
                    str(checkout / "scripts" / "setup-local.sh"),
                    "--with-estimator",
                ],
                cwd=checkout,
                env=environment,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(saved["estimator"]["sage_binary"], sage_path)
        self.assertTrue(
            saved["estimator"]["lattice_estimator_path"].endswith(
                ".external/lattice-estimator"
            )
        )
        self.assertTrue(
            saved["estimator"]["enhanced_lattice_estimator_path"].endswith(
                ".external/enhanced-lattice-estimator"
            )
        )

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
