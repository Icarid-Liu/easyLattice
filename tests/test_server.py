import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from app.server import EasyLatticeHandler


class ServerTests(unittest.TestCase):
    def test_local_server_serves_relative_browser_assets(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            for path, expected_type in (("/", "text/html"), ("/styles.css", "text/css"), ("/app.js", "javascript")):
                connection.request("GET", path)
                response = connection.getresponse()
                self.assertEqual(response.status, 200, path)
                self.assertIn(expected_type, response.getheader("Content-Type", ""), path)
                response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
