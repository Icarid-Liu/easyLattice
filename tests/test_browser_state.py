from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from app.server import EasyLatticeHandler

try:
    import websocket
except ImportError:  # pragma: no cover - exercised only on dependency-limited hosts
    websocket = None


CHROMIUM = next(
    (
        path
        for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
        if (path := shutil.which(name))
    ),
    None,
)


FETCH_HOOK = r"""
(() => {
  const response = (data, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
  });
  const config = {
    source: "browser-test",
    llm: { enabled: false, configured: false, provider: "", model: "" },
    estimator: {
      remote_configured: false,
      remote_url: null,
      remote_timeout_seconds: 60,
      sage_binary: "sage",
      lattice_estimator_path: null,
      version: null,
    },
  };
  window.__requests = [];
  window.fetch = (url, options = {}) => {
    if (String(url) === "/api/config/public") return Promise.resolve(response(config));
    return new Promise((resolve, reject) => {
      const entry = {
        url: String(url),
        body: options.body ? JSON.parse(options.body) : null,
        resolveResult(data, status = 200) {
          resolve(response(data, status));
        },
        rejectError(message) {
          reject(new Error(message));
        },
      };
      window.__requests.push(entry);
    });
  };
})();
"""


class CdpPage:
    def __init__(self, connection):
        self.connection = connection
        self.next_id = 0

    def command(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        request_id = self.next_id
        self.connection.send(
            json.dumps({"id": request_id, "method": method, "params": params or {}})
        )
        while True:
            message = json.loads(self.connection.recv())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise AssertionError(f"CDP {method} failed: {message['error']}")
            return message.get("result", {})

    def evaluate(self, expression: str):
        response = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        if "exceptionDetails" in response:
            raise AssertionError(response["exceptionDetails"])
        result = response["result"]
        if result.get("subtype") == "error":
            raise AssertionError(result.get("description", "browser evaluation failed"))
        return result.get("value")

    def wait_for(self, expression: str, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                value = self.evaluate(expression)
                if value:
                    return value
            except (AssertionError, KeyError) as exc:
                last_error = exc
            time.sleep(0.05)
        detail = f"; last error: {last_error}" if last_error else ""
        raise AssertionError(f"timed out waiting for {expression}{detail}")


@unittest.skipUnless(CHROMIUM, "Chromium browser is unavailable")
@unittest.skipUnless(websocket, "websocket-client is unavailable")
class BrowserRequestStateTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), EasyLatticeHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.stop_server)

        self.user_data = tempfile.TemporaryDirectory(prefix="ailattice-chromium-")
        self.addCleanup(self.user_data.cleanup)
        self.browser_log = tempfile.TemporaryFile(mode="w+b")
        self.addCleanup(self.browser_log.close)
        self.browser = subprocess.Popen(
            [
                CHROMIUM,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-first-run",
                "--no-proxy-server",
                "--safebrowsing-disable-auto-update",
                "--remote-allow-origins=*",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={self.user_data.name}",
                "about:blank",
            ],
            stdout=self.browser_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.addCleanup(self.stop_browser)

        debugging_port = self.wait_for_debugging_port()
        tabs = self.read_json(f"http://127.0.0.1:{debugging_port}/json")
        page = next(tab for tab in tabs if tab.get("type") == "page")
        with mock.patch.dict(
            os.environ,
            {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
        ):
            self.connection = websocket.create_connection(
                page["webSocketDebuggerUrl"],
                timeout=5,
            )
        self.addCleanup(self.connection.close)
        self.page = CdpPage(self.connection)
        self.page.command("Page.enable")
        self.page.command("Page.addScriptToEvaluateOnNewDocument", {"source": FETCH_HOOK})

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=3)

    def stop_browser(self):
        if self.browser.poll() is not None:
            return
        try:
            os.killpg(self.browser.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self.browser.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.browser.pid, signal.SIGKILL)
            self.browser.wait(timeout=5)

    def wait_for_debugging_port(self) -> int:
        port_file = Path(self.user_data.name) / "DevToolsActivePort"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if port_file.is_file():
                return int(port_file.read_text(encoding="utf-8").splitlines()[0])
            if self.browser.poll() is not None:
                break
            time.sleep(0.05)
        self.browser_log.seek(0)
        output = self.browser_log.read().decode("utf-8", errors="replace")
        self.fail(f"Chromium did not expose DevTools. Output:\n{output[-4000:]}")

    @staticmethod
    def read_json(url: str, timeout: float = 10.0):
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                with opener.open(url, timeout=1) as response:
                    return json.load(response)
            except Exception as exc:  # local endpoint may not be ready yet
                last_error = exc
                time.sleep(0.05)
        raise AssertionError(f"timed out reading {url}: {last_error}")

    def navigate(self, query: str):
        port = self.server.server_address[1]
        self.page.command("Page.navigate", {"url": f"http://127.0.0.1:{port}/{query}"})

    def test_request_state_interactions(self):
        self.navigate("?request-state-test=1")
        self.page.wait_for(
            "document.readyState === 'complete'"
            " && window.__requests.length === 1"
            " && searchState.snapshot().inFlight"
        )

        running_revision = self.page.evaluate("searchState.snapshot().revision")
        self.page.evaluate(
            """(() => {
              const select = document.querySelector('#language-select');
              select.value = 'zh';
              select.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  title: document.querySelector('#summary-title').textContent,
                  subtitle: document.querySelector('#summary-subtitle').textContent,
                  status: document.querySelector('#status-pill').textContent,
                  revision: searchState.snapshot().revision,
                }))()"""
            ),
            {
                "title": "正在搜索参数",
                "subtitle": "正在生成适合 NTT 的模数并筛选安全估计。",
                "status": "运行中",
                "revision": running_revision,
            },
        )

        self.page.evaluate(
            """(() => {
              const input = document.querySelector('#target-security');
              input.value = '129';
              input.dispatchEvent(new Event('input', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  inFlight: searchState.snapshot().inFlight,
                  submitDisabled: document.querySelector('#parameter-form button[type="submit"]').disabled,
                  status: document.querySelector('#status-pill').textContent,
                }))()"""
            ),
            {"inFlight": False, "submitDisabled": False, "status": "输入已更改"},
        )

        self.page.evaluate("document.querySelector('#parameter-form button[type=submit]').click()")
        self.page.wait_for("window.__requests.length === 2 && searchState.snapshot().inFlight")
        self.page.evaluate("window.__requests[0].resolveResult({ old: true })")
        time.sleep(0.1)
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  inFlight: searchState.snapshot().inFlight,
                  submitDisabled: document.querySelector('#parameter-form button[type="submit"]').disabled,
                  title: document.querySelector('#summary-title').textContent,
                }))()"""
            ),
            {"inFlight": True, "submitDisabled": True, "title": "正在搜索参数"},
        )

        self.page.evaluate(
            "document.querySelector('input[name=workspaceMode][value=dfr]').click()"
        )
        self.page.evaluate("window.__requests[1].rejectError('inactive failure')")
        self.page.wait_for("!searchState.snapshot().inFlight && searchState.snapshot().error")
        self.page.evaluate(
            "document.querySelector('input[name=workspaceMode][value=search]').click()"
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  title: document.querySelector('#summary-title').textContent,
                  subtitle: document.querySelector('#summary-subtitle').textContent,
                  status: document.querySelector('#status-pill').textContent,
                }))()"""
            ),
            {"title": "请求失败", "subtitle": "inactive failure", "status": "错误"},
        )

        self.page.evaluate(
            "document.querySelector('input[name=hardProblem][value=\"ntru:matrix\"]').click()"
        )
        for request_index, family in enumerate(("hps", "hrss", "ntru_prime"), start=2):
            if family != "hps":
                self.page.evaluate(
                    """(() => {
                      const select = document.querySelector('#ring-family');
                      select.value = 'power2';
                      select.dispatchEvent(new Event('input', { bubbles: true }));
                      select.dispatchEvent(new Event('change', { bubbles: true }));
                      document.querySelector('input[value="ntru:matrix"]').click();
                    })()"""
                )
            self.page.evaluate(
                f"""(() => {{
                  const select = document.querySelector('#ring-family');
                  select.value = '{family}';
                  select.dispatchEvent(new Event('input', {{ bubbles: true }}));
                  select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }})()"""
            )
            self.assertEqual(
                self.page.evaluate(
                    """(() => ({
                      matrixDisabled: document.querySelector('input[value="ntru:matrix"]').disabled,
                      variant: document.querySelector('input[name=hardProblem]:checked').value,
                    }))()"""
                ),
                {"matrixDisabled": True, "variant": "ntru:ring"},
            )
            if family == "hps":
                self.assertEqual(
                    self.page.evaluate(
                        """(() => {
                          const matrix = document.querySelector('input[value="ntru:matrix"]');
                          const ring = document.querySelector('input[value="ntru:ring"]');
                          const span = matrix.nextElementSibling;
                          const disabled = getComputedStyle(span);
                          const result = {
                            cursor: disabled.cursor,
                            opacity: disabled.opacity,
                            background: disabled.backgroundColor,
                          };
                          matrix.checked = true;
                          const checkedDisabled = getComputedStyle(span);
                          result.checkedCursor = checkedDisabled.cursor;
                          result.checkedOpacity = checkedDisabled.opacity;
                          result.checkedBackground = checkedDisabled.backgroundColor;
                          ring.checked = true;
                          return result;
                        })()"""
                    ),
                    {
                        "cursor": "not-allowed",
                        "opacity": "0.78",
                        "background": "rgb(237, 241, 239)",
                        "checkedCursor": "not-allowed",
                        "checkedOpacity": "0.82",
                        "checkedBackground": "rgb(223, 230, 227)",
                    },
                )

            self.page.evaluate("document.querySelector('#parameter-form button[type=submit]').click()")
            self.page.wait_for(f"window.__requests.length === {request_index + 1}")
            payload = self.page.evaluate(f"window.__requests[{request_index}].body")
            self.assertEqual(payload["hardProblemCategory"], "ntru")
            self.assertEqual(payload["hardProblemVariant"], "ring")
            self.assertEqual(payload["ringFamily"], family)
            self.page.evaluate(
                f"window.__requests[{request_index}].rejectError('checked {family}')"
            )
            self.page.wait_for("!searchState.snapshot().inFlight")

        self.navigate("?preview=1")
        self.page.wait_for(
            "document.readyState === 'complete'"
            " && typeof EasyLatticeModel === 'object'"
            " && searchState.snapshot().resultCurrent"
        )
        self.page.evaluate(
            """(() => {
              const select = document.querySelector('#language-select');
              select.value = 'en';
              select.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => {
                  const rows = Object.fromEntries([...document.querySelectorAll('#security-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  return {
                    status: document.querySelector('#status-pill').textContent,
                    validation: rows['Validation status'],
                    profile: rows['Estimator profile'],
                    attempted: rows['Candidates attempted'],
                    source: rows.Source,
                    next: rows.Next,
                    invalidText: /\b(undefined|null)\b/.test(document.body.innerText),
                    plaintextJson: Boolean(document.querySelector('#alternatives pre, #dfr-results pre')),
                  };
                })()"""
            ),
            {
                "status": "Fast screened",
                "validation": "Fast screened",
                "profile": "enhanced",
                "attempted": "0",
                "source": "Fast security screen",
                "next": "Bind this recommendation to concrete scheme constraints before use.",
                "invalidText": False,
                "plaintextJson": False,
            },
        )

        self.page.evaluate(
            """(() => {
              const select = document.querySelector('#language-select');
              select.value = 'zh';
              select.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  status: document.querySelector('#status-pill').textContent,
                  warning: document.querySelector('#warnings').textContent.includes('尚未绑定到具体方案'),
                  invalidText: /\b(undefined|null)\b/.test(document.body.innerText),
                }))()"""
            ),
            {"status": "已快速筛选", "warning": True, "invalidText": False},
        )

        self.page.evaluate(
            """(() => {
              const language = document.querySelector('#language-select');
              language.value = 'en';
              language.dispatchEvent(new Event('change', { bubbles: true }));
              document.querySelector('#use-estimator').click();
              document.querySelector('#parameter-form button[type=submit]').click();
            })()"""
        )
        self.page.wait_for(
            "searchState.snapshot().resultCurrent"
            " && searchState.snapshot().result.validation.status === 'failed'"
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => {
                  const rows = Object.fromEntries([...document.querySelectorAll('#security-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  return {
                    status: document.querySelector('#status-pill').textContent,
                    validation: rows['Validation status'],
                    source: rows.Source,
                    warning: document.querySelector('#warnings').textContent.includes('runtime or configuration is unavailable'),
                  };
                })()"""
            ),
            {
                "status": "Validation failed",
                "validation": "Validation failed",
                "source": "Fast security screen",
                "warning": True,
            },
        )
        self.page.evaluate(
            """(() => {
              const select = document.querySelector('#language-select');
              select.value = 'zh';
              select.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  status: document.querySelector('#status-pill').textContent,
                  warning: document.querySelector('#warnings').textContent.includes('运行环境或配置不可用'),
                }))()"""
            ),
            {"status": "验证失败", "warning": True},
        )

        self.page.evaluate(
            """(() => {
              const language = document.querySelector('#language-select');
              language.value = 'en';
              language.dispatchEvent(new Event('change', { bubbles: true }));
              document.querySelector('#use-estimator').click();
              document.querySelector('input[name=hardProblem][value="ntru:ring"]').click();
              const family = document.querySelector('#ring-family');
              family.value = 'ntru_prime';
              family.dispatchEvent(new Event('input', { bubbles: true }));
              family.dispatchEvent(new Event('change', { bubbles: true }));
              document.querySelector('#parameter-form button[type=submit]').click();
            })()"""
        )
        self.page.wait_for(
            "searchState.snapshot().resultCurrent"
            " && searchState.snapshot().result.recommendation.ring.preset === 'sntrup653'"
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => {
                  const instance = Object.fromEntries([...document.querySelectorAll('#instance-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  const security = Object.fromEntries([...document.querySelectorAll('#security-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  return {
                    preset: instance.Preset,
                    fixedWeight: instance['Fixed weight'],
                    ntruType: instance['NTRU type'],
                    nistCategory: security['NIST category'],
                    cyclotomic: 'Cyclotomic' in instance,
                    ntt: 'NTT' in instance,
                    split: 'Split' in instance,
                    nttQuality: 'NTT quality' in instance,
                    invalidText: /\b(undefined|null)\b/.test(document.querySelector('#search-results').innerText),
                  };
                })()"""
            ),
            {
                "preset": "sntrup653",
                "fixedWeight": "288",
                "ntruType": "circulant",
                "nistCategory": "1",
                "cyclotomic": False,
                "ntt": False,
                "split": False,
                "nttQuality": False,
                "invalidText": False,
            },
        )

        self.page.evaluate(
            "document.querySelector('input[name=workspaceMode][value=dfr]').click()"
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  single: document.querySelector('#dfr-single').textContent,
                  vector: document.querySelector('#dfr-vector').textContent,
                  type: dfrState.snapshot().result.type,
                }))()"""
            ),
            {"single": "-147.14", "vector": "-139.14", "type": "lwe"},
        )
        before_switch = self.page.evaluate("dfrState.snapshot().revision")
        self.assertFalse(
            self.page.evaluate("document.querySelector('#copy-dfr-json').disabled")
        )
        self.page.evaluate(
            "document.querySelector('input[name=dfrType][value=ntru]').click()"
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => {
                  const state = dfrState.snapshot();
                  return {
                    revision: state.revision,
                    resultCurrent: state.resultCurrent,
                    resultType: state.result && state.result.type,
                    copyDisabled: document.querySelector('#copy-dfr-json').disabled,
                  };
                })()"""
            ),
            {
                "revision": before_switch + 1,
                "resultCurrent": True,
                "resultType": "ntru",
                "copyDisabled": False,
            },
        )

        expected_rings = {
            "cyclic": ("x^509 - 1", "0", "1"),
            "negacyclic": ("x^509 + 1", "0", "509"),
            "ntru_prime": ("x^509 - x - 1", "1", "509"),
        }
        for ring_type, (polynomial, worst, profiles) in expected_rings.items():
            self.page.evaluate(
                f"""(() => {{
                  const select = document.querySelector('[name=dfrRingType]');
                  select.value = '{ring_type}';
                  select.dispatchEvent(new Event('input', {{ bubbles: true }}));
                  select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                  document.querySelector('#dfr-form button[type=submit]').click();
                }})()"""
            )
            self.page.wait_for(
                f"dfrState.snapshot().resultCurrent"
                f" && dfrState.snapshot().result.ring_type === '{ring_type}'"
            )
            rendered = self.page.evaluate(
                """(() => {
                  const rows = Object.fromEntries([...document.querySelectorAll('#dfr-calculation-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  return {
                    single: document.querySelector('#dfr-single').textContent,
                    vector: document.querySelector('#dfr-vector').textContent,
                    ringType: rows['Polynomial ring'],
                    polynomial: rows['Ring polynomial'],
                    worst: rows['Worst coefficient index'],
                    profiles: rows['Distinct coefficient profiles'],
                    warning: document.querySelector('#dfr-warnings').textContent,
                    invalidText: /\b(undefined|null)\b/.test(document.querySelector('#dfr-results').innerText),
                  };
                })()"""
            )
            self.assertEqual(rendered["single"], "-552.23")
            self.assertEqual(rendered["vector"], "-543.24")
            self.assertEqual(rendered["ringType"], ring_type)
            self.assertEqual(rendered["polynomial"], polynomial)
            self.assertEqual(rendered["worst"], worst)
            self.assertEqual(rendered["profiles"], profiles)
            self.assertIn("union bound", rendered["warning"])
            self.assertIn("outside this module", rendered["warning"])
            self.assertFalse(rendered["invalidText"])
            if ring_type == "ntru_prime":
                self.assertIn("makes no independence claim", rendered["warning"])

        self.page.evaluate(
            """(() => {
              const select = document.querySelector('#language-select');
              select.value = 'zh';
              select.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertTrue(
            self.page.evaluate(
                "document.querySelector('#dfr-warnings').textContent.includes('不作独立性假设')"
            )
        )


if __name__ == "__main__":
    unittest.main()
