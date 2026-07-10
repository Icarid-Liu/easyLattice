import json
import sys
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.local_runner import (
    RUNNER_TOKEN_HEADER,
    LocalRunnerState,
    RunnerJob,
    create_runner_server,
    runner_page_url,
    validate_estimator_root,
    validate_sage_binary,
)


class LocalRunnerTests(unittest.TestCase):
    def test_path_validation_and_isolated_configuration(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            estimator = root / "estimator"
            estimator.mkdir()
            (estimator / "__init__.py").write_text('__version__ = "test"\n', encoding="utf-8")

            self.assertEqual(validate_sage_binary(sys.executable), str(Path(sys.executable).resolve()))
            self.assertEqual(validate_estimator_root(root), str(root))
            with self.assertRaises(ValueError):
                validate_estimator_root(root / "missing")

            state = LocalRunnerState(sage_binary=sys.executable, lattice_estimator_path=str(root))
            config = state.configuration()
            self.assertEqual(config.source, "local-runner")
            self.assertEqual(config.estimator.sage_binary, str(Path(sys.executable).resolve()))
            self.assertEqual(config.estimator.lattice_estimator_path, str(root))
            self.assertTrue(state.status()["configured"])
            state.configure({"sageBinary": "", "latticeEstimatorPath": ""})
            self.assertFalse(state.status()["configured"])
            state.configure({"sageBinary": sys.executable, "latticeEstimatorPath": str(root)})
            self.assertTrue(state.status()["configured"])
            state.close()

    def test_jobs_keep_the_runner_configuration(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            estimator = root / "estimator"
            estimator.mkdir()
            (estimator / "__init__.py").write_text("", encoding="utf-8")
            state = LocalRunnerState(sage_binary=sys.executable, lattice_estimator_path=str(root))
            job = RunnerJob(id="job", payload={"targetSecurity": 128}, config=state.configuration())
            try:
                with patch("app.local_runner.recommend_with_agent", return_value={"ok": True}) as recommend:
                    state.run_job(job)
                recommend.assert_called_once_with(job.payload, config=job.config)
                self.assertEqual(job.status, "succeeded")
            finally:
                state.close()

    def test_token_protected_status_and_cors(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            estimator = root / "estimator"
            estimator.mkdir()
            (estimator / "__init__.py").write_text("", encoding="utf-8")
            state = LocalRunnerState(sage_binary=sys.executable, lattice_estimator_path=str(root))
            server = create_runner_server(state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
            try:
                connection.request("GET", "/api/runner/status")
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()

                connection.request(
                    "GET",
                    "/api/runner/status",
                    headers={RUNNER_TOKEN_HEADER: state.token, "Origin": "https://icarid-liu.github.io"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload["configured"])
                self.assertEqual(
                    response.headers["Access-Control-Allow-Origin"],
                    "https://icarid-liu.github.io",
                )
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                state.close()

    def test_runner_url_preserves_public_url_and_uses_loopback_api(self):
        page = runner_page_url(
            "https://icarid-liu.github.io/easyLattice/static/index.html?lang=zh",
            8123,
            "token",
        )
        self.assertIn("lang=zh", page)
        self.assertIn("apiBase=http%3A%2F%2F127.0.0.1%3A8123", page)
        self.assertIn("runnerToken=token", page)


if __name__ == "__main__":
    unittest.main()
