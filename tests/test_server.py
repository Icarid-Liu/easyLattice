import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from app.server import EasyLatticeHandler


class ServerTests(unittest.TestCase):
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
                self.assertIn(expected_type, response.getheader("Content-Type", ""), path)
                response.read()
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
