import json
import os
import socket
import threading
import time
import unittest
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import app.agent as agent_module
import app.server as server_module
from app.config import AppConfig, EstimatorConfig, LLMConfig
from app.job_progress import progress_reporting, report_progress
from app.local_profile import LocalProfileError
from app.server import EasyLatticeHandler


class ServerTests(unittest.TestCase):
    @contextmanager
    def running_server(self, host="127.0.0.1"):
        server = ThreadingHTTPServer((host, 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def request_json(self, server, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        encoded = body
        if isinstance(body, dict):
            encoded = json.dumps(body).encode("utf-8")
        try:
            connection.request(method, path, body=encoded, headers=headers or {})
            response = connection.getresponse()
            raw = response.read().decode("utf-8")
            return response, json.loads(raw) if raw else None
        finally:
            connection.close()

    def profile_headers(self, server, **overrides):
        host = f"127.0.0.1:{server.server_address[1]}"
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://{host}",
        }
        headers.update(overrides)
        return headers

    def clear_jobs(self):
        with server_module.jobs_lock:
            server_module.jobs.clear()

    def test_profile_origin_helpers_require_loopback_and_exact_http_origin(self):
        for host in ("127.0.0.1", "::1", "[::1]", "localhost", "LOCALHOST"):
            with self.subTest(host=host):
                self.assertTrue(server_module.is_loopback_host(host))
        for host in ("0.0.0.0", "192.0.2.1", "example.test", "localhost:8000"):
            with self.subTest(host=host):
                self.assertFalse(server_module.is_loopback_host(host))

        self.assertTrue(
            server_module.same_origin(
                "http://127.0.0.1:8000",
                "127.0.0.1:8000",
            )
        )
        self.assertFalse(
            server_module.same_origin(
                "https://127.0.0.1:8000",
                "127.0.0.1:8000",
            )
        )
        self.assertFalse(
            server_module.same_origin(
                "http://localhost:8000",
                "127.0.0.1:8000",
            )
        )
        self.assertFalse(
            server_module.same_origin(
                "http://127.0.0.1:8000/path?query=1",
                "127.0.0.1:8000",
            )
        )

    def test_profile_get_returns_stable_state_without_cors_or_error_leakage(self):
        state = {
            "ok": True,
            "sage_binary": "sage",
            "remote_configured": False,
            "profiles": {
                "standard": {
                    "available": True,
                    "path": "/standard",
                    "commit": "01234567",
                    "dirty": False,
                    "error_code": None,
                    "message": None,
                },
                "enhanced": {
                    "available": False,
                    "path": None,
                    "commit": None,
                    "dirty": None,
                    "error_code": "estimator_profile_not_configured",
                    "message": "enhanced estimator path is not configured.",
                },
            },
        }
        with self.running_server() as server:
            with (
                mock.patch.dict(os.environ, {"EASYLATTICE_ALLOWED_ORIGINS": "*"}),
                mock.patch("app.server.local_profile_state", return_value=state),
            ):
                response, payload = self.request_json(
                    server,
                    "GET",
                    "/api/config/estimator-profile",
                    headers={"Origin": "https://attacker.example"},
                )

            self.assertEqual(response.status, 200)
            self.assertEqual(payload, state)
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

            with mock.patch(
                "app.server.local_profile_state",
                side_effect=RuntimeError("secret local path /private/estimator"),
            ):
                response, payload = self.request_json(
                    server,
                    "GET",
                    "/api/config/estimator-profile",
                )

            self.assertEqual(response.status, 500)
            self.assertEqual(payload["code"], "config_read_failed")
            self.assertNotIn("secret", json.dumps(payload))
            self.assertNotIn("Traceback", json.dumps(payload))

    def test_profile_post_persists_only_profile_fields_on_same_origin(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.local.json"
            standard = root / "standard"
            (standard / "estimator").mkdir(parents=True)
            (standard / "estimator" / "__init__.py").write_text("", encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    {
                        "estimator": {
                            "remote_timeout_seconds": 99,
                            "remote_url": None,
                        },
                        "llm": {"enabled": False},
                        "unrelated": {"keep": True},
                    }
                ),
                encoding="utf-8",
            )
            request = {
                "sage_binary": "sage",
                "lattice_estimator_path": str(standard),
                "enhanced_lattice_estimator_path": None,
            }

            def available_record(estimator, profile):
                path = (
                    estimator.lattice_estimator_path
                    if profile == "standard"
                    else estimator.enhanced_lattice_estimator_path
                )
                if path is None:
                    return {
                        "available": False,
                        "path": None,
                        "commit": None,
                        "dirty": None,
                        "error_code": "estimator_profile_not_configured",
                        "message": "not configured",
                    }
                return {
                    "available": True,
                    "path": path,
                    "commit": "01234567",
                    "dirty": False,
                    "error_code": None,
                    "message": None,
                }

            with self.running_server() as server:
                with (
                    mock.patch.dict(
                        os.environ,
                        {
                            "EASYLATTICE_CONFIG": str(config_path),
                            "EASYLATTICE_ALLOWED_ORIGINS": "*",
                        },
                    ),
                    mock.patch(
                        "app.local_profile.profile_record",
                        side_effect=available_record,
                    ),
                ):
                    response, payload = self.request_json(
                        server,
                        "POST",
                        "/api/config/estimator-profile",
                        request,
                        self.profile_headers(server),
                    )

            self.assertEqual(response.status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["profiles"]["standard"]["commit"], "01234567")
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["estimator"]["sage_binary"], "sage")
            self.assertEqual(
                saved["estimator"]["lattice_estimator_path"],
                str(standard.resolve()),
            )
            self.assertIsNone(saved["estimator"]["enhanced_lattice_estimator_path"])
            self.assertEqual(saved["estimator"]["remote_timeout_seconds"], 99)
            self.assertEqual(saved["llm"], {"enabled": False})
            self.assertEqual(saved["unrelated"], {"keep": True})

    def test_profile_post_rejects_non_loopback_and_unsafe_request_metadata(self):
        cases = (
            ("missing-origin", {}, 403, "local_configuration_disabled"),
            (
                "https-origin",
                {"Origin": "https://127.0.0.1:{port}"},
                403,
                "local_configuration_disabled",
            ),
            (
                "mismatched-origin",
                {"Origin": "http://localhost:{port}"},
                403,
                "local_configuration_disabled",
            ),
            (
                "non-json",
                {"Content-Type": "text/plain"},
                400,
                "invalid_profile_request",
            ),
        )
        request = {
            "sage_binary": "sage",
            "lattice_estimator_path": "/unused",
            "enhanced_lattice_estimator_path": None,
        }
        with self.running_server() as server:
            with (
                mock.patch.dict(os.environ, {"EASYLATTICE_ALLOWED_ORIGINS": "*"}),
                mock.patch("app.server.save_local_profile") as save,
            ):
                for name, overrides, expected_status, expected_code in cases:
                    with self.subTest(name=name):
                        headers = self.profile_headers(server)
                        if name == "missing-origin":
                            headers.pop("Origin")
                        headers.update(
                            {
                                key: value.format(port=server.server_address[1])
                                for key, value in overrides.items()
                            }
                        )
                        response, payload = self.request_json(
                            server,
                            "POST",
                            "/api/config/estimator-profile",
                            request,
                            headers,
                        )
                        self.assertEqual(response.status, expected_status)
                        self.assertEqual(payload["code"], expected_code)
                        self.assertIsNone(
                            response.getheader("Access-Control-Allow-Origin")
                        )
                save.assert_not_called()

        with self.running_server("0.0.0.0") as server:
            with mock.patch("app.server.save_local_profile") as save:
                response, payload = self.request_json(
                    server,
                    "POST",
                    "/api/config/estimator-profile",
                    request,
                    self.profile_headers(server),
                )
            self.assertEqual(response.status, 403)
            self.assertEqual(payload["code"], "local_configuration_disabled")
            save.assert_not_called()

    def test_profile_post_enforces_16_kib_limit_before_profile_logic(self):
        with self.running_server() as server:
            connection = HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
                timeout=2,
            )
            host = f"127.0.0.1:{server.server_address[1]}"
            try:
                with mock.patch("app.server.save_local_profile") as save:
                    connection.putrequest(
                        "POST",
                        "/api/config/estimator-profile",
                    )
                    connection.putheader("Content-Type", "application/json")
                    connection.putheader("Origin", f"http://{host}")
                    connection.putheader(
                        "Content-Length",
                        str(server_module.PROFILE_MAX_REQUEST_BODY_BYTES + 1),
                    )
                    connection.endheaders()
                    response = connection.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 413)
                self.assertIn("16384", payload["error"])
                self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
                save.assert_not_called()
            finally:
                connection.close()

    def test_profile_errors_use_stable_statuses_without_tracebacks(self):
        cases = (
            ("invalid_profile_request", 400),
            ("sage_not_found", 400),
            ("config_write_failed", 500),
        )
        request = {
            "sage_binary": "sage",
            "lattice_estimator_path": "/unused",
            "enhanced_lattice_estimator_path": None,
        }
        with self.running_server() as server:
            for code, expected_status in cases:
                with self.subTest(code=code):
                    error = LocalProfileError(
                        code,
                        "validation failed",
                        field="lattice_estimator_path",
                    )
                    with mock.patch(
                        "app.server.save_local_profile",
                        side_effect=error,
                    ):
                        response, payload = self.request_json(
                            server,
                            "POST",
                            "/api/config/estimator-profile",
                            request,
                            self.profile_headers(server),
                        )
                    self.assertEqual(response.status, expected_status)
                    self.assertEqual(
                        payload,
                        {
                            "ok": False,
                            "code": code,
                            "error": "validation failed",
                            "field": "lattice_estimator_path",
                        },
                    )
                    self.assertNotIn("Traceback", json.dumps(payload))

            with mock.patch(
                "app.server.save_local_profile",
                side_effect=RuntimeError("secret /private/estimator"),
            ):
                response, payload = self.request_json(
                    server,
                    "POST",
                    "/api/config/estimator-profile",
                    request,
                    self.profile_headers(server),
                )
            self.assertEqual(response.status, 500)
            self.assertEqual(payload["code"], "config_write_failed")
            self.assertNotIn("secret", json.dumps(payload))

    def test_profile_options_never_emits_cors_headers(self):
        with self.running_server() as server:
            with mock.patch.dict(
                os.environ,
                {"EASYLATTICE_ALLOWED_ORIGINS": "*"},
            ):
                response, payload = self.request_json(
                    server,
                    "OPTIONS",
                    "/api/config/estimator-profile",
                    headers={
                        "Origin": "https://attacker.example",
                        "Access-Control-Request-Method": "POST",
                    },
                )
            self.assertEqual(response.status, 204)
            self.assertIsNone(payload)
            for header in (
                "Access-Control-Allow-Origin",
                "Access-Control-Allow-Methods",
                "Access-Control-Allow-Headers",
            ):
                self.assertIsNone(response.getheader(header))

    def test_agent_job_preflight_rejects_missing_standard_and_enhanced_profiles(self):
        self.clear_jobs()
        cases = (
            (
                "standard",
                {"problem": "ntru", "useEstimator": True},
            ),
            (
                "enhanced",
                {
                    "request": {
                        "problem": "rlwe",
                        "hardProblemCategory": "lwe",
                        "hardProblemVariant": "mlwe",
                        "useEstimator": True,
                    }
                },
            ),
        )
        try:
            with self.running_server() as server:
                for expected_profile, request in cases:
                    with self.subTest(profile=expected_profile):
                        with mock.patch(
                            "app.local_profile.load_config",
                            return_value=AppConfig(estimator=EstimatorConfig()),
                        ):
                            response, payload = self.request_json(
                                server,
                                "POST",
                                "/api/agent/jobs",
                                request,
                                {"Content-Type": "application/json"},
                            )
                        self.assertEqual(response.status, 409)
                        self.assertEqual(
                            payload["code"],
                            "estimator_profile_not_configured",
                        )
                        self.assertEqual(payload["required_profile"], expected_profile)
                        self.assertNotIn(payload.get("job_id"), server_module.jobs)
        finally:
            self.clear_jobs()

    def test_agent_job_preflight_bypasses_disabled_remote_and_unknown_variants(self):
        self.clear_jobs()
        cases = (
            (
                "disabled",
                {"problem": "rlwe", "useEstimator": False},
                AppConfig(estimator=EstimatorConfig()),
            ),
            (
                "remote",
                {"problem": "rlwe", "useEstimator": True},
                AppConfig(
                    estimator=EstimatorConfig(remote_url="http://worker.example")
                ),
            ),
            (
                "unknown",
                {
                    "request": {
                        "problem": "unsupported",
                        "hardProblemVariant": "unknown",
                        "useEstimator": True,
                    }
                },
                AppConfig(estimator=EstimatorConfig()),
            ),
        )
        try:
            with self.running_server() as server:
                with mock.patch("app.server.submit_job") as submit:
                    for name, request, config in cases:
                        with self.subTest(name=name):
                            with mock.patch(
                                "app.local_profile.load_config",
                                return_value=config,
                            ):
                                response, payload = self.request_json(
                                    server,
                                    "POST",
                                    "/api/agent/jobs",
                                    request,
                                    {"Content-Type": "application/json"},
                                )
                            self.assertEqual(response.status, 202)
                            self.assertEqual(payload["status"], "queued")
                            self.assertIsNone(payload["stage"])
                            self.assertIsNone(payload["estimator_profile"])
                            self.assertIsNone(payload["estimator_commit"])
                            with server_module.jobs_lock:
                                server_module.jobs.pop(payload["job_id"], None)
                    self.assertEqual(submit.call_count, len(cases))
        finally:
            self.clear_jobs()

    def test_run_job_tracks_stages_and_does_not_leak_progress_between_jobs(self):
        first = server_module.RecommendationJob(id="first", payload={})
        snapshots = []

        def successful_recommend(_payload):
            report_progress("candidate_search")
            snapshots.append((first.stage, first.estimator_profile, first.estimator_commit))
            report_progress("estimator_running", "enhanced", "89abcdef")
            snapshots.append((first.stage, first.estimator_profile, first.estimator_commit))
            report_progress("finalizing")
            snapshots.append((first.stage, first.estimator_profile, first.estimator_commit))
            return {"ok": True}

        with mock.patch(
            "app.server.recommend_with_agent",
            side_effect=successful_recommend,
        ):
            server_module.run_job(first)

        self.assertEqual(
            snapshots,
            [
                ("candidate_search", None, None),
                ("estimator_running", "enhanced", "89abcdef"),
                ("finalizing", "enhanced", "89abcdef"),
            ],
        )
        self.assertEqual(first.status, "succeeded")
        self.assertEqual(first.result, {"ok": True})
        self.assertEqual(first.stage, "finalizing")
        self.assertEqual(first.estimator_profile, "enhanced")
        self.assertEqual(first.estimator_commit, "89abcdef")

        second = server_module.RecommendationJob(id="second", payload={})

        def second_recommend(_payload):
            report_progress("candidate_search")
            report_progress("finalizing")
            return {"ok": True, "job": 2}

        with mock.patch(
            "app.server.recommend_with_agent",
            side_effect=second_recommend,
        ):
            server_module.run_job(second)

        self.assertEqual(second.status, "succeeded")
        self.assertEqual(second.stage, "finalizing")
        self.assertIsNone(second.estimator_profile)
        self.assertIsNone(second.estimator_commit)

        failed = server_module.RecommendationJob(id="failed", payload={})

        def failing_recommend(_payload):
            report_progress("candidate_search")
            raise RuntimeError("search failed")

        with mock.patch(
            "app.server.recommend_with_agent",
            side_effect=failing_recommend,
        ):
            server_module.run_job(failed)

        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.stage, "candidate_search")
        self.assertIn("RuntimeError: search failed", failed.error)
        self.assertIsNone(failed.result)

    def test_agent_reports_search_and_finalizing_for_deterministic_and_llm_paths(self):
        deterministic_events = []
        with (
            progress_reporting(deterministic_events.append),
            mock.patch(
                "app.agent.run_deterministic_search",
                return_value={"ok": True},
            ),
        ):
            result = agent_module.recommend_with_agent({}, config=AppConfig())

        self.assertTrue(result["ok"])
        self.assertEqual(
            [event.stage for event in deterministic_events],
            ["candidate_search", "finalizing"],
        )

        llm_events = []
        llm = mock.Mock()
        llm.interpret_request.return_value = SimpleNamespace(
            overrides={"targetSecurityBits": 192},
            explanation="parsed",
        )
        config = AppConfig(llm=LLMConfig(enabled=True))
        with (
            progress_reporting(llm_events.append),
            mock.patch("app.agent.OpenAICompatibleLLM", return_value=llm),
            mock.patch(
                "app.agent.run_deterministic_search",
                return_value={"ok": True},
            ) as search,
        ):
            result = agent_module.recommend_with_agent(
                {"intent": "stronger", "useLLM": True},
                config=config,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [event.stage for event in llm_events],
            ["candidate_search", "finalizing"],
        )
        self.assertEqual(search.call_args.args[0]["targetSecurityBits"], 192)

    def assert_no_new_handler_threads(self, existing_threads):
        deadline = time.monotonic() + 1
        handlers = []
        while time.monotonic() < deadline:
            handlers = [
                item
                for item in threading.enumerate()
                if item not in existing_threads
                and "process_request_thread" in item.name
                and item.is_alive()
            ]
            if not handlers:
                break
            time.sleep(0.01)
        self.assertEqual(handlers, [])

    def test_drip_feed_post_body_hits_total_deadline_without_thread_leak(self):
        class RecordingHandler(EasyLatticeHandler):
            timeout_before_response = object()

            def write_request_timeout(self, message):
                type(self).timeout_before_response = self.connection.gettimeout()
                super().write_request_timeout(message)

        server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        self.assertTrue(server.daemon_threads)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        existing_threads = set(threading.enumerate())
        client = socket.create_connection(server.server_address, timeout=2)
        client.settimeout(2)
        stop_drip = threading.Event()
        sent_chunks = []

        def drip_body():
            while not stop_drip.wait(0.02):
                try:
                    client.sendall(b"x")
                except OSError:
                    break
                sent_chunks.append(1)

        feeder = threading.Thread(target=drip_body, daemon=True)
        try:
            request = (
                b"POST /api/decryption-failure/calculate HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 128\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )
            started = time.monotonic()
            with mock.patch.object(
                server_module,
                "POST_BODY_READ_DEADLINE_SECONDS",
                0.12,
            ):
                client.sendall(request)
                feeder.start()
                response = bytearray()
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
            elapsed = time.monotonic() - started
            stop_drip.set()
            feeder.join(timeout=1)

            headers, body = bytes(response).split(b"\r\n\r\n", 1)
            self.assertIn(b" 408 ", headers)
            self.assertEqual(
                json.loads(body.decode("utf-8"))["error"],
                "Request body read timed out.",
            )
            self.assertGreaterEqual(len(sent_chunks), 3)
            self.assertGreaterEqual(elapsed, 0.09)
            self.assertLess(elapsed, 1.5)
            self.assertIsNone(RecordingHandler.timeout_before_response)
            self.assertEqual(RecordingHandler.rbufsize, 0)
            self.assertFalse(feeder.is_alive())

            self.assert_no_new_handler_threads(existing_threads)
        finally:
            stop_drip.set()
            client.close()
            if feeder.is_alive():
                feeder.join(timeout=1)
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def request_preparse_timeout_with_drip(self, initial_bytes):
        class RecordingHandler(EasyLatticeHandler):
            timeout_before_response = object()

            def write_preparse_timeout(self, message):
                type(self).timeout_before_response = self.connection.gettimeout()
                super().write_preparse_timeout(message)

        server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        existing_threads = set(threading.enumerate())
        client = socket.create_connection(server.server_address, timeout=2)
        client.settimeout(2)
        stop_drip = threading.Event()
        sent_chunks = []

        def drip_header():
            while not stop_drip.wait(0.02):
                try:
                    client.sendall(b"x")
                except OSError:
                    break
                sent_chunks.append(1)

        feeder = threading.Thread(target=drip_header, daemon=True)
        try:
            started = time.monotonic()
            with (
                mock.patch.object(
                    server_module,
                    "REQUEST_HEADER_READ_DEADLINE_SECONDS",
                    0.12,
                ),
                mock.patch.dict(
                    os.environ,
                    {"EASYLATTICE_ALLOWED_ORIGINS": "*"},
                ),
            ):
                client.sendall(initial_bytes)
                feeder.start()
                response = bytearray()
                connection_closed = False
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        connection_closed = True
                        break
                    response.extend(chunk)
            elapsed = time.monotonic() - started
            stop_drip.set()
            feeder.join(timeout=1)

            self.assertGreaterEqual(len(sent_chunks), 3)
            self.assertGreaterEqual(elapsed, 0.09)
            self.assertLess(elapsed, 1.5)
            self.assertIsNone(RecordingHandler.timeout_before_response)
            self.assertFalse(feeder.is_alive())
            self.assertTrue(connection_closed)
            self.assert_no_new_handler_threads(existing_threads)
            return bytes(response)
        finally:
            stop_drip.set()
            client.close()
            if feeder.is_alive():
                feeder.join(timeout=1)
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def assert_valid_preparse_timeout_response(self, response):
        headers, body = response.split(b"\r\n\r\n", 1)
        self.assertTrue(headers.startswith(b"HTTP/1.1 408 Request Timeout\r\n"))
        self.assertIn(b"\r\nConnection: close", headers)
        self.assertIn(b"\r\nAccess-Control-Allow-Origin: *", headers)
        self.assertNotIn(b"attacker.example", headers)
        header_fields = {
            name.strip().lower(): value.strip()
            for name, value in (
                line.split(b":", 1)
                for line in headers.split(b"\r\n")[1:]
            )
        }
        self.assertEqual(int(header_fields[b"content-length"]), len(body))
        self.assertEqual(
            header_fields[b"content-type"],
            b"application/json; charset=utf-8",
        )
        payload = json.loads(
            body.decode("utf-8"),
            parse_constant=lambda value: self.fail(value),
        )
        self.assertEqual(payload, {
            "ok": False,
            "error": "Request headers read timed out.",
        })

    def test_drip_feed_incomplete_request_line_returns_valid_408(self):
        response = self.request_preparse_timeout_with_drip(b"POST /api/")
        self.assert_valid_preparse_timeout_response(response)

    def test_drip_feed_incomplete_headers_returns_valid_408(self):
        response = self.request_preparse_timeout_with_drip(
            b"POST /api/decryption-failure/calculate HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Origin: https://attacker.example\r\n"
            b"X-Drip: "
        )
        self.assert_valid_preparse_timeout_response(response)

    def test_valid_post_and_get_reuse_keep_alive_connection(self):
        class KeepAliveHandler(EasyLatticeHandler):
            protocol_version = "HTTP/1.1"

        server = ThreadingHTTPServer(("127.0.0.1", 0), KeepAliveHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        try:
            with (
                mock.patch.object(
                    server_module,
                    "REQUEST_HEADER_READ_DEADLINE_SECONDS",
                    0.5,
                ),
                mock.patch.object(
                    server_module,
                    "POST_BODY_READ_DEADLINE_SECONDS",
                    0.5,
                ),
                mock.patch(
                    "app.server.recommend_with_agent",
                    return_value={"ok": True},
                ),
            ):
                connection.request(
                    "POST",
                    "/api/rlwe/recommend",
                    body=b'{"targetSecurityBits": 128}',
                    headers={"Content-Type": "application/json"},
                )
                post_response = connection.getresponse()
                post_payload = json.loads(post_response.read().decode("utf-8"))
                reused_socket = connection.sock

                connection.request("GET", "/api/health")
                get_response = connection.getresponse()
                get_payload = json.loads(get_response.read().decode("utf-8"))

            self.assertEqual(post_response.status, 200)
            self.assertTrue(post_payload["ok"])
            self.assertIs(connection.sock, reused_socket)
            self.assertEqual(get_response.status, 200)
            self.assertTrue(get_payload["ok"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_all_post_endpoints_reject_invalid_or_oversized_content_lengths(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request_without_body(path, content_length):
            connection = HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
                timeout=1,
            )
            connection.putrequest("POST", path)
            connection.putheader("Content-Type", "application/json")
            if content_length is not None:
                connection.putheader("Content-Length", content_length)
            connection.endheaders()
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            connection.close()
            return response.status, body

        paths = (
            "/api/decryption-failure/calculate",
            "/api/agent/jobs",
            "/api/rlwe/recommend",
            "/api/agent/recommend",
        )
        cases = (
            ("missing", None, 400),
            ("invalid", "abc", 400),
            ("negative", "-1", 400),
            ("zero", "0", 400),
            (
                "oversized",
                str(server_module.MAX_REQUEST_BODY_BYTES + 1),
                413,
            ),
        )
        try:
            with (
                mock.patch("app.server.recommend_with_agent", return_value={"ok": True}),
                mock.patch("app.server.submit_job"),
            ):
                for path in paths:
                    for name, content_length, expected_status in cases:
                        with self.subTest(path=path, case=name):
                            status, body = request_without_body(path, content_length)
                            self.assertEqual(status, expected_status)
                            self.assertFalse(body["ok"])
                            self.assertIn("Content-Length", body["error"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_server_never_emits_nonfinite_json_constants(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            with mock.patch(
                "app.server.recommend_with_agent",
                return_value={
                    "ok": True,
                    "finite": 17.5,
                    "diagnostics": [float("nan"), float("inf")],
                },
            ):
                connection.request(
                    "POST",
                    "/api/rlwe/recommend",
                    body=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                raw = response.read().decode("utf-8")

            self.assertEqual(response.status, 200)
            self.assertNotIn("NaN", raw)
            self.assertNotIn("Infinity", raw)
            payload = json.loads(raw, parse_constant=lambda value: self.fail(value))
            self.assertEqual(payload["finite"], 17.5)
            self.assertEqual(payload["diagnostics"], [None, None])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_api_requests_reject_nonfinite_json_constants_before_logic(self):
        zero = {"type": "custom_pmf", "pmf": {"0": 1}}
        valid_dfr = {
            "type": "ntru",
            "n": 1,
            "p0": 0,
            "p1": 0,
            "p2": 0,
            "p3": 0,
            "delta": 1,
            "g": zero,
            "f": zero,
            "s": zero,
            "e": zero,
            "m": zero,
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)

        def post(path, body):
            connection.request(
                "POST",
                path,
                body=body.encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))

        try:
            dfr_body = json.dumps(valid_dfr)[:-1] + ', "unused": NaN}'
            with mock.patch("app.server.calculate_decryption_failure") as calculate:
                status, payload = post("/api/decryption-failure/calculate", dfr_body)

            self.assertEqual(status, 400)
            self.assertEqual(
                payload["error"],
                "non-finite JSON constant is not allowed: NaN",
            )
            calculate.assert_not_called()

            with mock.patch("app.server.recommend_with_agent") as recommend:
                for constant in ("NaN", "Infinity", "-Infinity"):
                    with self.subTest(constant=constant):
                        status, payload = post(
                            "/api/rlwe/recommend",
                            f'{{"targetSecurityBits": {constant}}}',
                        )
                        self.assertEqual(status, 400)
                        self.assertEqual(
                            payload["error"],
                            f"non-finite JSON constant is not allowed: {constant}",
                        )

            recommend.assert_not_called()

            with mock.patch(
                "app.server.recommend_with_agent",
                return_value={"ok": True},
            ) as recommend:
                status, payload = post(
                    "/api/rlwe/recommend",
                    '{"targetSecurityBits": 128.5}',
                )

            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            received = recommend.call_args.args[0]
            self.assertIsInstance(received["targetSecurityBits"], float)
            self.assertEqual(received["targetSecurityBits"], 128.5)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_hostile_dfr_scalars_return_400_instead_of_500(self):
        zero = {"type": "custom_pmf", "pmf": {"0": "1"}}
        base = {
            "type": "ntru",
            "n": 1,
            "p0": 0,
            "p1": 0,
            "p2": 0,
            "p3": 0,
            "delta": 1,
            "g": zero,
            "f": zero,
            "s": zero,
            "e": zero,
            "m": zero,
        }
        encoded_base = json.dumps(base)
        hostile_bodies = (
            ("quoted-positive", json.dumps(base | {"p0": "1e10000000"})),
            ("quoted-negative", json.dumps(base | {"delta": "1e-10000000"})),
            (
                "unquoted-positive",
                encoded_base.replace('"p0": 0', '"p0": 1e10000000', 1),
            ),
            (
                "unquoted-negative",
                encoded_base.replace('"delta": 1', '"delta": 1e-10000000', 1),
            ),
            ("gaussian-mean", json.dumps(base | {
                "e": {"type": "discrete_gaussian", "stddev": "1", "mean": "1e10000000"},
            })),
            ("gaussian-stddev", json.dumps(base | {
                "e": {"type": "discrete_gaussian", "stddev": "1e-10000000"},
            })),
            ("pmf-support", json.dumps(base | {
                "e": {"type": "custom_pmf", "pmf": {"1e10000000": "1"}},
            })),
            ("pmf-probability", json.dumps(base | {
                "e": {"type": "custom_pmf", "pmf": {"0": "1e-10000000"}},
            })),
            ("nested-pmf-unquoted", json.dumps(base | {
                "e": {
                    "type": "custom_pmf",
                    "pmf": '{"0": 1, "1": 1e-10000000}',
                },
            })),
            ("long-text", json.dumps(base | {"p0": "9" * 100_000})),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            for name, body in hostile_bodies:
                with self.subTest(name=name):
                    connection.request(
                        "POST",
                        "/api/decryption-failure/calculate",
                        body=body.encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    response = connection.getresponse()
                    response_payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 400)
                    self.assertFalse(response_payload["ok"])
                    self.assertIn("supported", response_payload["error"])
                    self.assertNotIn("Overflow", response_payload["error"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_dfr_preserves_numeric_lexemes_without_changing_recommendation_json_types(self):
        zero = {"type": "custom_pmf", "pmf": {"0": 1}}
        valid_dfr = {
            "type": "ntru",
            "n": 1,
            "p0": 0,
            "p1": 0,
            "p2": 0,
            "p3": 0,
            "delta": 1,
            "g": zero,
            "f": zero,
            "s": zero,
            "e": zero,
            "m": zero,
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            connection.request(
                "POST",
                "/api/decryption-failure/calculate",
                body=json.dumps(valid_dfr).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            dfr_response = connection.getresponse()
            dfr_payload = json.loads(dfr_response.read().decode("utf-8"))
            self.assertEqual(dfr_response.status, 200)
            self.assertEqual(dfr_payload["dimensions"], {"n": 1})

            recommendation = {"targetSecurityBits": 128.5}
            with mock.patch(
                "app.server.recommend_with_agent",
                return_value={"ok": True},
            ) as recommend:
                connection.request(
                    "POST",
                    "/api/rlwe/recommend",
                    body=json.dumps(recommendation).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                response.read()

            self.assertEqual(response.status, 200)
            received = recommend.call_args.args[0]
            self.assertIsInstance(received["targetSecurityBits"], float)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_index_loads_browser_model_before_app_controller(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertLess(body.index('src="app-model.js"'), body.index('src="app.js"'))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()

    def test_local_server_serves_relative_browser_assets(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            for path, expected_type in (
                ("/", "text/html"),
                ("/styles.css", "text/css"),
                ("/app-model.js", "javascript"),
                ("/app.js", "javascript"),
                ("/preview-data.js", "javascript"),
                ("/static/app-model.js", "javascript"),
                ("/static/app.js", "javascript"),
            ):
                connection.request("GET", path)
                response = connection.getresponse()
                self.assertEqual(response.status, 200, path)
                content_type = response.getheader("Content-Type", "")
                self.assertIn(expected_type, content_type, path)
                if path.endswith(".js"):
                    self.assertEqual(content_type, "text/javascript; charset=utf-8", path)
                self.assertEqual(response.getheader("Cache-Control"), "no-store", path)
                response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()

    def test_frontend_uses_one_form_invalidation_path_and_shared_state_model(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            connection.request("GET", "/app.js")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(body.count("EasyLatticeModel.createRequestState()"), 2)
            self.assertIn('form.addEventListener("input", markSearchInputsChanged);', body)
            self.assertIn('dfrForm.addEventListener("input", markDfrInputsChanged);', body)
            self.assertNotIn('form.addEventListener("change", markSearchInputsChanged);', body)
            self.assertNotIn('dfrForm.addEventListener("change", markDfrInputsChanged);', body)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()

    def test_static_assets_reject_traversal(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            for path in (
                "/static/../app/server.py",
                "/static/%2e%2e/app/server.py",
                "/static/does-not-exist.js",
            ):
                connection.request("GET", path)
                response = connection.getresponse()
                self.assertEqual(response.status, 404, path)
                self.assertIn("application/json", response.getheader("Content-Type", ""), path)
                response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
