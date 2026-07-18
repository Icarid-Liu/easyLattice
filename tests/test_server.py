import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest import mock

import app.server as server_module
from app.server import EasyLatticeHandler


class ServerTests(unittest.TestCase):
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
