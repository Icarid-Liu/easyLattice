import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from app.server import EasyLatticeHandler


class ServerTests(unittest.TestCase):
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
        hostile_payloads = (
            base | {"p0": "1e10000000"},
            base | {"delta": "1e-10000000"},
            base | {
                "e": {"type": "discrete_gaussian", "stddev": "1", "mean": "1e10000000"},
            },
            base | {
                "e": {"type": "discrete_gaussian", "stddev": "1e-10000000"},
            },
            base | {
                "e": {"type": "custom_pmf", "pmf": {"1e10000000": "1"}},
            },
            base | {
                "e": {"type": "custom_pmf", "pmf": {"0": "1e-10000000"}},
            },
            base | {"p0": "9" * 100_000},
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            for payload in hostile_payloads:
                with self.subTest(payload=list(payload.keys())):
                    body = json.dumps(payload).encode("utf-8")
                    connection.request(
                        "POST",
                        "/api/decryption-failure/calculate",
                        body=body,
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
