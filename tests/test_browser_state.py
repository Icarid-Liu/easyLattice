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
from dataclasses import asdict
from decimal import Decimal, localcontext
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from app.decryption_failure import calculate_decryption_failure
from app.ntru_search import recommend_ntru
from app.parameter_search import (
    RequestOptions,
    candidate_rank,
    make_candidate,
    sparse_ternary_spec,
)
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

DFR_BERNOULLI = {"type": "custom_pmf", "pmf": {"0": "0.5", "1": "0.5"}}
DFR_ZERO = {"type": "custom_pmf", "pmf": {"0": "1"}}
LWE_DFR_PAYLOAD = {
    "type": "lwe",
    "m": 512,
    "n": 256,
    "delta": "832",
    "precisionBits": 512,
    "tailBits": 128,
    "s": {"type": "centered_binomial", "eta": "3"},
    "e": {"type": "centered_binomial", "eta": "3"},
    "e1": {"type": "centered_binomial", "eta": "2"},
    "r": {"type": "centered_binomial", "eta": "3"},
    "e2": {"type": "centered_binomial", "eta": "2"},
    "ec1": {"type": "kyber_nearest_compression", "q": "3329", "d": "10"},
    "ec2": {"type": "kyber_nearest_compression", "q": "3329", "d": "4"},
}


def bounded_noncyclic_dfr_payload(ring_type: str) -> dict:
    return {
        "type": "ntru",
        "ringType": ring_type,
        "n": 64,
        "delta": "40",
        "p0": "1",
        "p1": "0",
        "p2": "0",
        "p3": "0",
        "precisionBits": 512,
        "tailBits": 128,
        "g": DFR_BERNOULLI,
        "s": DFR_BERNOULLI,
        "f": DFR_ZERO,
        "e": DFR_ZERO,
        "m": DFR_ZERO,
    }


def assert_nested_key_contract(
    testcase: unittest.TestCase,
    preview: object,
    backend: object,
    path: str = "candidate",
    *,
    top_level_extra: set[str] | None = None,
) -> None:
    if isinstance(preview, dict) and isinstance(backend, dict):
        expected = set(backend)
        if path == "candidate" and top_level_extra:
            expected |= top_level_extra
        testcase.assertEqual(set(preview), expected, path)
        for key in set(backend) & set(preview):
            assert_nested_key_contract(
                testcase,
                preview[key],
                backend[key],
                f"{path}.{key}",
            )
    elif (
        isinstance(preview, list)
        and isinstance(backend, list)
        and preview
        and backend
        and isinstance(preview[0], dict)
        and isinstance(backend[0], dict)
    ):
        for index, item in enumerate(preview):
            assert_nested_key_contract(
                testcase,
                item,
                backend[min(index, len(backend) - 1)],
                f"{path}[{index}]",
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

    def set_viewport(self, width: int, height: int, *, mobile: bool):
        self.page.command(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": mobile,
                "screenWidth": width,
                "screenHeight": height,
            },
        )

    def layout_snapshot(self):
        return self.page.evaluate(
            r"""(() => {
              const tolerance = 2;
              const visible = (node) => {
                if (!node) return false;
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0
                  && rect.height > 0;
              };
              const withinViewport = (node) => {
                const rect = node.getBoundingClientRect();
                return rect.left >= -tolerance && rect.right <= innerWidth + tolerance;
              };
              const overlaps = (first, second) => {
                const a = first.getBoundingClientRect();
                const b = second.getBoundingClientRect();
                return a.left < b.right - tolerance
                  && a.right > b.left + tolerance
                  && a.top < b.bottom - tolerance
                  && a.bottom > b.top + tolerance;
              };

              const keySelectors = [
                '.app-shell',
                '.control-panel',
                '.brand-row',
                '#language-select',
                '#parameter-form',
                '#dfr-form',
                '.workspace',
                '.top-strip',
                '#status-pill',
                '#search-results',
                '#result-grid',
                '#details',
                '#alternatives',
                '#dfr-results',
              ];
              const outOfViewport = keySelectors.filter((selector) => {
                const node = document.querySelector(selector);
                return visible(node) && !withinViewport(node);
              });

              const overflowingTextControls = [
                ...document.querySelectorAll('button, .segmented span, .problem-options span'),
              ].filter((node) => visible(node) && (
                node.scrollWidth > node.clientWidth + tolerance
                || node.scrollHeight > node.clientHeight + tolerance
              )).map((node) => node.id || node.textContent.trim());
              const overflowNodes = [...document.querySelectorAll('*')].filter((node) => {
                if (!visible(node)) return false;
                const rect = node.getBoundingClientRect();
                return rect.left < -tolerance
                  || rect.right > innerWidth + tolerance
                  || node.scrollWidth > node.clientWidth + tolerance;
              }).slice(0, 12).map((node) => ({
                node: node.id || node.className || node.tagName,
                left: Math.round(node.getBoundingClientRect().left),
                right: Math.round(node.getBoundingClientRect().right),
                clientWidth: node.clientWidth,
                scrollWidth: node.scrollWidth,
              }));

              const overlapFailures = [];
              [
                ['.brand-row > div:not(.brand-mark)', '#language-select', 'brand/language'],
                ['.top-strip > div', '#status-pill', 'heading/status'],
                ['.control-panel', '.workspace', 'control/workspace'],
              ].forEach(([firstSelector, secondSelector, label]) => {
                const first = document.querySelector(firstSelector);
                const second = document.querySelector(secondSelector);
                if (visible(first) && visible(second) && overlaps(first, second)) {
                  overlapFailures.push(label);
                }
              });
              [
                '#result-grid > .metric-panel',
                '#details > .detail-column',
                '#candidate-list > .candidate',
                '#dfr-results .result-grid > .metric-panel',
                '#dfr-results .details-layout > .detail-column',
              ].forEach((selector) => {
                const nodes = [...document.querySelectorAll(selector)].filter(visible);
                for (let first = 0; first < nodes.length; first += 1) {
                  for (let second = first + 1; second < nodes.length; second += 1) {
                    if (overlaps(nodes[first], nodes[second])) {
                      overlapFailures.push(`${selector}:${first}/${second}`);
                    }
                  }
                }
              });

              return {
                innerWidth,
                innerHeight,
                documentScrollWidth: document.documentElement.scrollWidth,
                bodyScrollWidth: document.body.scrollWidth,
                documentOverflow: document.documentElement.scrollWidth > innerWidth + tolerance,
                bodyOverflow: document.body.scrollWidth > innerWidth + tolerance,
                outOfViewport,
                overflowNodes,
                overflowingTextControls,
                overlapFailures,
              };
            })()"""
        )

    def assert_viewport_layout(self, width: int, height: int):
        snapshot = self.layout_snapshot()
        self.assertEqual(snapshot["innerWidth"], width, snapshot)
        self.assertEqual(snapshot["innerHeight"], height, snapshot)
        self.assertFalse(snapshot["documentOverflow"], snapshot)
        self.assertFalse(snapshot["bodyOverflow"], snapshot)
        self.assertEqual(snapshot["outOfViewport"], [], snapshot)
        self.assertEqual(snapshot["overflowNodes"], [], snapshot)
        self.assertEqual(snapshot["overflowingTextControls"], [], snapshot)
        self.assertEqual(snapshot["overlapFailures"], [], snapshot)

    def test_preview_dfr_fixtures_match_backend_contract(self):
        self.set_viewport(1440, 1000, mobile=False)
        self.navigate("?preview=1")
        self.page.wait_for(
            "document.readyState === 'complete'"
            " && window.EASYLATTICE_PREVIEW_FIXTURES?.dfr?.ntru_rings"
        )

        cyclic_request = self.page.evaluate(
            "window.EASYLATTICE_PREVIEW_FIXTURES.dfr.requests.ntru.cyclic"
        )
        self.assertEqual(
            self.page.evaluate(
                "window.EASYLATTICE_PREVIEW_FIXTURES.dfr.ntru_rings.cyclic"
            ),
            calculate_decryption_failure(cyclic_request),
        )

        for ring_type in ("negacyclic", "ntru_prime"):
            with self.subTest(ring_type=ring_type):
                preview = self.page.evaluate(
                    f"window.EASYLATTICE_PREVIEW_FIXTURES.dfr.ntru_rings.{ring_type}"
                )
                backend = calculate_decryption_failure(
                    bounded_noncyclic_dfr_payload(ring_type)
                )
                self.assertEqual(preview, backend)

        self.assertEqual(
            self.page.evaluate("window.EASYLATTICE_PREVIEW_FIXTURES.dfr.lwe"),
            calculate_decryption_failure(LWE_DFR_PAYLOAD),
        )
        self.assertEqual(
            self.page.evaluate("window.EASYLATTICE_PREVIEW_FIXTURES.dfr.requests.lwe"),
            LWE_DFR_PAYLOAD,
        )

        distribution_summaries = self.page.evaluate(
            """(() => {
              const fixtures = window.EASYLATTICE_PREVIEW_FIXTURES.dfr;
              return {
                cyclic: fixtures.ntru.distributions,
                lwe: fixtures.lwe.distributions,
              };
            })()"""
        )
        for fixture_name, summaries in distribution_summaries.items():
            with self.subTest(fixture=fixture_name):
                self.assertTrue(summaries)
                for name, summary in summaries.items():
                    self.assertIn(
                        "tail_probability_upper_bound",
                        summary,
                        f"{fixture_name}.{name}",
                    )
                    self.assertEqual(summary["tail_probability_upper_bound"], "0")

    def test_preview_candidate_contracts_are_internally_consistent(self):
        self.set_viewport(1440, 1000, mobile=False)
        self.navigate("?preview=1")
        self.page.wait_for(
            "document.readyState === 'complete'"
            " && typeof window.EASYLATTICE_PREVIEW_FIXTURES?.recommendation === 'function'"
        )

        lwe_request = RequestOptions()
        lwe_distribution = sparse_ternary_spec(1024, 4, 2)
        backend_lwe_candidates = []
        for q in (12289, 13313, 15361):
            candidate = make_candidate(
                1024,
                q,
                lwe_distribution,
                lwe_distribution,
                lwe_request,
            )
            candidate_rank(candidate, lwe_request)
            backend_lwe_candidates.append(candidate)

        preview_default = self.page.evaluate(
            """(() => window.EASYLATTICE_PREVIEW_FIXTURES.recommendation({
              hardProblemCategory: 'lwe',
              hardProblemVariant: 'rlwe',
              ringFamily: 'power2',
              targetSecurity: 128,
              securityModel: 'classical',
              redCostModel: 'matzov',
              nttScalePower: 0,
              minQBits: 2,
              maxQBits: 24,
              distribution: 'auto',
              secretDistribution: 'auto',
              errorDistribution: 'auto',
              useEstimator: false,
              intent: '',
              useLLM: false,
            }))()"""
        )
        for field, value in asdict(lwe_request).items():
            self.assertEqual(preview_default["request"][field], value, field)
        self.assertEqual(preview_default["request"]["problem"], "rlwe")
        self.assertEqual(preview_default["request"]["intent"], "")
        self.assertFalse(preview_default["request"]["use_llm"])
        for section in (
            "ring",
            "modulus",
            "distribution",
            "security",
            "selection",
            "visual_scores",
        ):
            self.assertEqual(
                preview_default["recommendation"][section],
                json.loads(json.dumps(backend_lwe_candidates[0][section])),
                section,
            )

        backend_ntru_candidates = {}
        for family in ("power2", "hps", "hrss", "ntru_prime"):
            response = recommend_ntru(
                {
                    "targetSecurity": 128,
                    "hardProblemCategory": "ntru",
                    "hardProblemVariant": "ring",
                    "ringFamily": family,
                    "securityModel": "classical",
                    "redCostModel": "matzov",
                    "nttScalePower": 0,
                    "minQBits": 2,
                    "maxQBits": 24,
                    "distribution": "auto",
                    "useEstimator": False,
                }
            )
            backend_ntru_candidates[family] = [
                response["recommendation"],
                *response["alternatives"],
            ]

        cases = (
            ("lwe", "power2", 105216, 105216),
            ("ntru", "power2", 5, 4),
            ("ntru", "hps", 4, 4),
            ("ntru", "hrss", 4, 4),
            ("ntru", "ntru_prime", 6, 6),
        )
        expected_alternatives = {
            ("lwe", "power2"): [
                (1024, 13313, 132.9, 117.8),
                (1024, 15361, 130.3, 115.4),
            ],
            ("ntru", "power2"): [
                (512, 10753, 130.2, None),
                (512, 11777, 128.4, None),
            ],
            ("ntru", "hps"): [
                (598, 2048, 129.9, None),
                (606, 2048, 131.8, None),
            ],
            ("ntru", "hrss"): [
                (676, 8192, 131.6, None),
                (682, 8192, 133.1, None),
            ],
            ("ntru", "ntru_prime"): [
                (761, 4591, 153, 139),
                (857, 5167, 175, 159),
            ],
        }
        for category, family, generated, eligible in cases:
            for security_model in ("classical", "quantum"):
                with self.subTest(
                    category=category,
                    family=family,
                    security_model=security_model,
                ):
                    result = self.page.evaluate(
                        f"""(() => window.EASYLATTICE_PREVIEW_FIXTURES.recommendation({{
                          hardProblemCategory: '{category}',
                          hardProblemVariant: '{'ring' if category == 'ntru' else 'rlwe'}',
                          ringFamily: '{family}',
                          targetSecurity: 128,
                          securityModel: '{security_model}',
                          redCostModel: 'matzov',
                          useEstimator: false,
                        }}))()"""
                    )
                    self.assertEqual(result["search"]["generated_candidates"], generated)
                    self.assertEqual(result["validation"]["eligible_candidates"], eligible)
                    self.assertTrue(result["alternatives"])
                    self.assertEqual(
                        [
                            (
                                candidate["ring"]["n"],
                                candidate["modulus"]["q"],
                                candidate["security"]["matzov_bits"],
                                candidate["security"]["matzov_quantum_bits"],
                            )
                            for candidate in result["alternatives"]
                        ],
                        expected_alternatives[(category, family)],
                    )
                    self.assertEqual(
                        len({
                            (
                                candidate["ring"]["n"],
                                candidate["modulus"]["q"],
                                candidate["distribution"]["name"],
                            )
                            for candidate in [
                                result["recommendation"],
                                *result["alternatives"],
                            ]
                        }),
                        len(result["alternatives"]) + 1,
                    )
                    for candidate in [result["recommendation"], *result["alternatives"]]:
                        q = candidate["modulus"]["q"]
                        selected = candidate["selection"]["selected_security_bits"]
                        expected_selected = candidate["security"][
                            "matzov_quantum_bits"
                            if security_model == "quantum"
                            else "matzov_bits"
                        ]
                        expected_level = (
                            "unclassified"
                            if selected is None
                            else "below NIST-I"
                            if selected < 128
                            else "NIST-I"
                            if selected < 192
                            else "NIST-III"
                            if selected < 256
                            else "NIST-V"
                        )
                        self.assertEqual(candidate["modulus"]["bits"], (q - 1).bit_length())
                        self.assertIn(str(q), candidate["ring"]["quotient"])
                        self.assertEqual(candidate["ring"]["family_id"], family)
                        self.assertEqual(selected, expected_selected)
                        self.assertEqual(candidate["selection"]["security_level"], expected_level)
                        self.assertEqual(
                            candidate["selection"]["status"],
                            "target_met"
                            if selected is not None and selected >= 128
                            else "target_unmet",
                        )

                        backend_pool = (
                            backend_ntru_candidates[family]
                            if category == "ntru"
                            else backend_lwe_candidates
                        )
                        backend_candidate = next(
                            item
                            for item in backend_pool
                            if item["ring"]["n"] == candidate["ring"]["n"]
                            and item["modulus"]["q"] == candidate["modulus"]["q"]
                            and item["distribution"]["name"]
                            == candidate["distribution"]["name"]
                        )
                        assert_nested_key_contract(
                            self,
                            candidate,
                            backend_candidate,
                            top_level_extra={"problem"} if category == "lwe" else None,
                        )

        quantum_lwe = self.page.evaluate(
            """(() => window.EASYLATTICE_PREVIEW_FIXTURES.recommendation({
              hardProblemCategory: 'lwe',
              hardProblemVariant: 'rlwe',
              ringFamily: 'power2',
              targetSecurity: 128,
              securityModel: 'quantum',
              redCostModel: 'matzov',
              useEstimator: false,
            }).recommendation)()"""
        )
        self.assertEqual(quantum_lwe["selection"]["selected_security_bits"], 119.4)
        self.assertEqual(quantum_lwe["selection"]["security_level"], "below NIST-I")

        for family in ("power2", "hps", "hrss"):
            unavailable = self.page.evaluate(
                f"""(() => window.EASYLATTICE_PREVIEW_FIXTURES.recommendation({{
                  hardProblemCategory: 'ntru',
                  hardProblemVariant: 'ring',
                  ringFamily: '{family}',
                  targetSecurity: 140,
                  securityModel: 'quantum',
                  redCostModel: 'matzov',
                  useEstimator: true,
                }}))()"""
            )
            self.assertIn(
                "quantum_estimate_unavailable",
                unavailable["validation"]["message_codes"],
            )
            self.assertIn(
                "validation_config_missing",
                unavailable["validation"]["message_codes"],
            )
            self.assertEqual(
                unavailable["validation"]["message"],
                "No quantum security estimate is available for this NTRU candidate.",
            )
            for candidate in [
                unavailable["recommendation"],
                *unavailable["alternatives"],
            ]:
                self.assertIn(
                    "quantum_estimate_unavailable",
                    candidate["warning_codes"],
                )
                self.assertIn(
                    "validation_config_missing",
                    candidate["warning_codes"],
                )

        failed_lwe = self.page.evaluate(
            """(() => window.EASYLATTICE_PREVIEW_FIXTURES.recommendation({
              hardProblemCategory: 'lwe',
              hardProblemVariant: 'rlwe',
              ringFamily: 'power2',
              targetSecurity: 128,
              securityModel: 'classical',
              redCostModel: 'matzov',
              useEstimator: true,
            }))()"""
        )
        for candidate in [failed_lwe["recommendation"], *failed_lwe["alternatives"]]:
            self.assertIn("validation_config_missing", candidate["warning_codes"])

        self.page.evaluate(
            """(() => {
              const result = window.EASYLATTICE_PREVIEW_FIXTURES.recommendation({
                hardProblemCategory: 'lwe',
                hardProblemVariant: 'rlwe',
                ringFamily: 'power2',
                targetSecurity: 140,
                securityModel: 'classical',
                redCostModel: 'matzov',
                useEstimator: false,
              });
              searchState.setResult(result);
              renderSearchState();
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  margin: document.querySelector('#security-margin').textContent,
                  detailedMargin: Object.fromEntries([...document.querySelectorAll('#security-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent])).Margin,
                  alternatives: document.querySelector('#candidate-list').textContent,
                  malformed: document.querySelector('#search-results').textContent.includes('+-'),
                }))()"""
            ),
            {
                "margin": "margin -5.4 bits",
                "detailedMargin": "-5.4 bits",
                "alternatives": (
                    "n=1024, q=13313, ST(l0=4, l1=2)"
                    "Classical: 132.9 bits · margin -7.1 bits · Target unmet"
                    "one_layer_remaining · 1024 | q - 1; 2048 does not divide q - 1"
                    "n=1024, q=15361, ST(l0=4, l1=2)"
                    "Classical: 130.3 bits · margin -9.7 bits · Target unmet"
                    "one_layer_remaining · 1024 | q - 1; 2048 does not divide q - 1"
                ),
                "malformed": False,
            },
        )
        self.page.evaluate(
            """(() => {
              const language = document.querySelector('#language-select');
              language.value = 'zh';
              language.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  margin: document.querySelector('#security-margin').textContent,
                  detailedMargin: Object.fromEntries([...document.querySelectorAll('#security-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]))['余量'],
                  alternativeHasMargin: document.querySelector('#candidate-list').textContent
                    .includes('余量 -7.1 比特'),
                  malformed: document.querySelector('#search-results').textContent.includes('+-'),
                }))()"""
            ),
            {
                "margin": "余量 -5.4 比特",
                "detailedMargin": "-5.4 比特",
                "alternativeHasMargin": True,
                "malformed": False,
            },
        )
        self.page.evaluate(
            """(() => {
              const language = document.querySelector('#language-select');
              language.value = 'en';
              language.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )

        self.page.evaluate(
            """(() => {
              const result = window.EASYLATTICE_PREVIEW_FIXTURES.recommendation({
                hardProblemCategory: 'ntru',
                hardProblemVariant: 'ring',
                ringFamily: 'hps',
                targetSecurity: 140,
                securityModel: 'quantum',
                redCostModel: 'matzov',
                useEstimator: false,
              });
              searchState.setResult(result);
              renderSearchState();
            })()"""
        )
        self.assertIn(
            "Quantum: security estimate unavailable · Target unmet",
            self.page.evaluate("document.querySelector('#candidate-list').textContent"),
        )

        self.page.evaluate(
            """(() => {
              document.querySelector('input[name=securityModel][value=quantum]').click();
              document.querySelector('#parameter-form button[type=submit]').click();
            })()"""
        )
        self.page.wait_for(
            "searchState.snapshot().resultCurrent"
            " && searchState.snapshot().result.recommendation.selection.security_model"
            " === 'quantum'"
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  level: document.querySelector('#security-level').textContent,
                  status: document.querySelector('#status-pill').textContent,
                  target: Object.fromEntries([...document.querySelectorAll('#security-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent])).Target,
                }))()"""
            ),
            {
                "level": "Below NIST-I",
                "status": "Target unmet",
                "target": "128 bits (Quantum)",
            },
        )
        self.page.evaluate(
            """(() => {
              const language = document.querySelector('#language-select');
              language.value = 'zh';
              language.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  level: document.querySelector('#security-level').textContent,
                  status: document.querySelector('#status-pill').textContent,
                  target: Object.fromEntries([...document.querySelectorAll('#security-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]))['目标'],
                }))()"""
            ),
            {
                "level": "低于 NIST-I",
                "status": "未达到目标",
                "target": "128 比特（量子）",
            },
        )

    def test_request_state_interactions(self):
        self.set_viewport(1440, 1000, mobile=False)
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

        self.page.evaluate(
            "document.querySelector('#parameter-form button[type=submit]').click()"
        )
        self.page.wait_for("window.__requests.length === 6 && searchState.snapshot().inFlight")
        self.page.evaluate("window.__requests[5].resolveResult({}, 503)")
        self.page.wait_for("!searchState.snapshot().inFlight && searchState.snapshot().error")
        self.assertEqual(
            self.page.evaluate(
                "document.querySelector('#summary-subtitle').textContent"
            ),
            "请求失败",
        )

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
                  const instance = Object.fromEntries([...document.querySelectorAll('#instance-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  const result = searchState.snapshot().result;
                  return {
                    status: document.querySelector('#status-pill').textContent,
                    validation: rows['Validation status'],
                    profile: rows['Estimator profile'],
                    attempted: rows['Candidates attempted'],
                    eligible: rows['Candidates eligible'],
                    generated: searchState.snapshot().result.search.generated_candidates,
                    source: rows.Source,
                    next: rows.Next,
                    nttQuality: instance['NTT quality'],
                    alternativeText: document.querySelector('#candidate-list').textContent,
                    previewWarning: document.querySelector('#warnings').textContent,
                    invalidText: /\b(undefined|null)\b/.test(document.body.innerText),
                    plaintextJson: Boolean(document.querySelector('#alternatives pre, #dfr-results pre')),
                    requestMatchesForm: result.request.target_security === Number(document.querySelector('[name=targetSecurity]').value)
                      && result.request.ntt_scale_power === Number(document.querySelector('[name=nttScalePower]').value)
                      && result.request.min_q_bits === Number(document.querySelector('[name=minQBits]').value)
                      && result.request.max_q_bits === Number(document.querySelector('[name=maxQBits]').value)
                      && result.request.secret_distribution === document.querySelector('[name=secretDistribution]').value
                      && result.request.error_distribution === document.querySelector('[name=errorDistribution]').value,
                    locked: [
                      document.querySelector('[name=targetSecurity]').readOnly,
                      document.querySelector('[name=nttScalePower]').disabled,
                      document.querySelector('[name=minQBits]').readOnly,
                      document.querySelector('[name=maxQBits]').readOnly,
                      document.querySelector('[name=secretDistribution]').disabled,
                      document.querySelector('[name=errorDistribution]').disabled,
                      document.querySelector('[name=intent]').readOnly,
                    ].every(Boolean),
                    selectorsInteractive: !document.querySelector('#language-select').disabled
                      && !document.querySelector('#ring-family').disabled
                      && !document.querySelector('[name=securityModel][value=classical]').disabled,
                  };
                })()"""
            ),
            {
                "status": "Fast screened",
                "validation": "Fast screened",
                "profile": "enhanced",
                "attempted": "0",
                "eligible": "105216",
                "generated": 105216,
                "source": "Fast security screen",
                "next": "Bind this recommendation to concrete scheme constraints before use.",
                "nttQuality": "full_split, remaining layers 0",
                "alternativeText": (
                    "n=1024, q=13313, ST(l0=4, l1=2)"
                    "Classical: 132.9 bits · margin +4.9 bits · Target met"
                    "one_layer_remaining · 1024 | q - 1; 2048 does not divide q - 1"
                    "n=1024, q=15361, ST(l0=4, l1=2)"
                    "Classical: 130.3 bits · margin +2.3 bits · Target met"
                    "one_layer_remaining · 1024 | q - 1; 2048 does not divide q - 1"
                ),
                "previewWarning": (
                    "This fast screen is not bound to a concrete scheme; scheme-level "
                    "correctness and security constraints remain unchecked."
                    "This is static preview data; run the local service for live security "
                    "and DFR calculations."
                ),
                "invalidText": False,
                "plaintextJson": False,
                "requestMatchesForm": True,
                "locked": True,
                "selectorsInteractive": True,
            },
        )
        self.assert_viewport_layout(1440, 1000)

        self.page.evaluate(
            """(() => {
              const select = document.querySelector('#language-select');
              select.value = 'zh';
              select.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => {
                  const rows = Object.fromEntries([...document.querySelectorAll('#instance-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  const security = Object.fromEntries([...document.querySelectorAll('#security-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  return {
                    status: document.querySelector('#status-pill').textContent,
                    warning: document.querySelector('#warnings').textContent.includes('尚未绑定到具体方案'),
                    previewWarning: document.querySelector('#warnings').textContent
                      .includes('静态预览数据仅用于演示'),
                    englishWarning: /This is|Preview data/.test(
                      document.querySelector('#warnings').textContent
                    ),
                    alternativeText: document.querySelector('#candidate-list').textContent,
                    estimatorStatuses: ['queued', 'running', 'succeeded', 'failed']
                      .map(estimatorJobStatusText),
                    fallbackError: localizeErrorMessage('request failed'),
                    nttQuality: rows['NTT 质量'],
                    localizedModels: ['MATZOV（经典）', 'MATZOV（量子）', 'ADPS16（经典）', 'ADPS16（量子）']
                      .every((label) => label in security),
                    rawModels: [...document.querySelectorAll('#security-list dt')]
                      .some((node) => /\((classical|quantum)\)/.test(node.textContent)),
                    signedMargin: security['余量'],
                    invalidText: /\b(undefined|null)\b/.test(document.body.innerText),
                  };
                })()"""
            ),
            {
                "status": "已快速筛选",
                "warning": True,
                "previewWarning": True,
                "englishWarning": False,
                "alternativeText": (
                    "n=1024, q=13313, ST(l0=4, l1=2)"
                    "经典：132.9 比特 · 余量 +4.9 比特 · 已达到目标"
                    "one_layer_remaining · 1024 | q - 1; 2048 does not divide q - 1"
                    "n=1024, q=15361, ST(l0=4, l1=2)"
                    "经典：130.3 比特 · 余量 +2.3 比特 · 已达到目标"
                    "one_layer_remaining · 1024 | q - 1; 2048 does not divide q - 1"
                ),
                "estimatorStatuses": ["排队中", "运行中", "已完成", "失败"],
                "fallbackError": "请求失败",
                "nttQuality": "full_split，剩余层数：0",
                "localizedModels": True,
                "rawModels": False,
                "signedMargin": "+6.6 比特",
                "invalidText": False,
            },
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
                    eligible: rows['Candidates eligible'],
                    generated: searchState.snapshot().result.search.generated_candidates,
                    warning: document.querySelector('#warnings').textContent.includes('runtime or configuration is unavailable'),
                  };
                })()"""
            ),
            {
                "status": "Validation failed",
                "validation": "Validation failed",
                "source": "Fast security screen",
                "eligible": "105216",
                "generated": 105216,
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
            })()"""
        )
        family_cases = (
            {
                "family": "power2",
                "requested_variant": "matrix",
                "effective_variant": "matrix",
                "ntru_type": "matrix",
                "family_name": "2-power cyclotomic NTRU",
                "n": 512,
                "polynomial": "x^512 + 1",
                "quotient": "Z_7681[x] / (x^512 + 1)",
                "q": 7681,
                "bits": 13,
                "cyclotomic": 1024,
                "preset": None,
                "generated": 5,
                "eligible": 4,
            },
            {
                "family": "power2",
                "requested_variant": "ring",
                "effective_variant": "ring",
                "ntru_type": "circulant",
                "family_name": "2-power cyclotomic NTRU",
                "n": 512,
                "polynomial": "x^512 + 1",
                "quotient": "Z_7681[x] / (x^512 + 1)",
                "q": 7681,
                "bits": 13,
                "cyclotomic": 1024,
                "preset": None,
                "generated": 5,
                "eligible": 4,
            },
            {
                "family": "hps",
                "requested_variant": "matrix",
                "effective_variant": "ring",
                "ntru_type": "circulant",
                "family_name": "NTRU-HPS style",
                "n": 592,
                "polynomial": "x^593 - 1 with one relation removed by the estimator",
                "quotient": "NTRU-HPS style mod q=2048, public polynomial degree N=593",
                "q": 2048,
                "bits": 11,
                "cyclotomic": None,
                "preset": None,
                "generated": 4,
                "eligible": 4,
            },
            {
                "family": "hrss",
                "requested_variant": "matrix",
                "effective_variant": "ring",
                "ntru_type": "circulant",
                "family_name": "NTRU-HRSS style",
                "n": 672,
                "polynomial": "x^673 - 1 with one relation removed by the estimator",
                "quotient": "NTRU-HRSS style mod q=8192, public polynomial degree N=673",
                "q": 8192,
                "bits": 13,
                "cyclotomic": None,
                "preset": None,
                "generated": 4,
                "eligible": 4,
            },
            {
                "family": "ntru_prime",
                "requested_variant": "matrix",
                "effective_variant": "ring",
                "ntru_type": "circulant",
                "family_name": "Streamlined NTRU Prime",
                "n": 653,
                "polynomial": "x^653 - x - 1",
                "quotient": "Z_4621[x] / (x^653 - x - 1)",
                "q": 4621,
                "bits": 13,
                "cyclotomic": None,
                "preset": "sntrup653",
                "generated": 6,
                "eligible": 6,
            },
        )
        for expected in family_cases:
            family = expected["family"]
            requested_variant = expected["requested_variant"]
            self.page.evaluate(
                f"""(() => {{
                  document.querySelector('input[name=hardProblem][value="ntru:ring"]').click();
                  const family = document.querySelector('#ring-family');
                  family.value = 'power2';
                  family.dispatchEvent(new Event('input', {{ bubbles: true }}));
                  family.dispatchEvent(new Event('change', {{ bubbles: true }}));
                  document.querySelector(
                    'input[name=hardProblem][value="ntru:{requested_variant}"]'
                  ).click();
                  family.value = '{family}';
                  family.dispatchEvent(new Event('input', {{ bubbles: true }}));
                  family.dispatchEvent(new Event('change', {{ bubbles: true }}));
                  document.querySelector('#parameter-form button[type=submit]').click();
                }})()"""
            )
            self.page.wait_for(
                f"searchState.snapshot().resultCurrent"
                f" && searchState.snapshot().result.recommendation.ring.family_id === '{family}'"
                f" && searchState.snapshot().result.request.hard_problem_variant"
                f" === '{expected['effective_variant']}'"
            )
            actual = self.page.evaluate(
                """(() => {
                  const result = searchState.snapshot().result;
                  const candidate = result.recommendation;
                  const instance = Object.fromEntries([...document.querySelectorAll('#instance-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  return {
                    family: candidate.ring.family_id,
                    familyName: candidate.ring.family,
                    n: candidate.ring.n,
                    polynomial: candidate.ring.polynomial,
                    quotient: candidate.ring.quotient,
                    q: candidate.modulus.q,
                    bits: candidate.modulus.bits,
                    cyclotomic: candidate.ring.cyclotomic_index,
                    ntruType: candidate.ring.ntru_type,
                    preset: candidate.ring.preset,
                    effectiveVariant: result.request.hard_problem_variant,
                    generated: result.search.generated_candidates,
                    eligible: result.validation.eligible_candidates,
                    alternativesUseFamily: result.alternatives.every(
                      (alternative) => alternative.ring.family_id === candidate.ring.family_id
                    ),
                    alternativeSelectionsMatch: result.alternatives.every((alternative) => {
                      const selection = alternative.selection;
                      const expected = selection.security_model === 'quantum'
                        ? alternative.security.matzov_quantum_bits
                        : alternative.security.matzov_bits;
                      return selection.selected_security_bits === expected;
                    }),
                    renderedFamily: instance.Family,
                    renderedNtruType: instance['NTRU type'],
                    invalidText: /\b(undefined|null)\b/.test(document.querySelector('#search-results').innerText),
                  };
                })()"""
            )
            self.assertEqual(
                actual,
                {
                    "family": family,
                    "familyName": expected["family_name"],
                    "n": expected["n"],
                    "polynomial": expected["polynomial"],
                    "quotient": expected["quotient"],
                    "q": expected["q"],
                    "bits": expected["bits"],
                    "cyclotomic": expected["cyclotomic"],
                    "ntruType": expected["ntru_type"],
                    "preset": expected["preset"],
                    "effectiveVariant": expected["effective_variant"],
                    "generated": expected["generated"],
                    "eligible": expected["eligible"],
                    "alternativesUseFamily": True,
                    "alternativeSelectionsMatch": True,
                    "renderedFamily": expected["family_name"],
                    "renderedNtruType": expected["ntru_type"],
                    "invalidText": False,
                },
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
            """(() => {
              const candidate = searchState.snapshot().result.recommendation;
              candidate.warning_codes.push('quantum_estimate_unavailable');
              renderSearchState();
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  localized: document.querySelector('#warnings').textContent
                    .includes('No quantum security estimate is available for this NTRU candidate.'),
                  machineCode: document.querySelector('#warnings').textContent
                    .includes('quantum_estimate_unavailable'),
                }))()"""
            ),
            {"localized": True, "machineCode": False},
        )
        self.page.evaluate(
            """(() => {
              const candidate = searchState.snapshot().result.recommendation;
              candidate.warning_codes = candidate.warning_codes
                .filter((code) => code !== 'quantum_estimate_unavailable');
              candidate.warnings.push(
                'No quantum security estimate is available for this NTRU candidate.'
              );
              const language = document.querySelector('#language-select');
              language.value = 'zh';
              language.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  localized: document.querySelector('#warnings').textContent
                    .includes('该 NTRU 候选没有可用的量子安全估计'),
                  english: document.querySelector('#warnings').textContent
                    .includes('No quantum security estimate'),
                }))()"""
            ),
            {"localized": True, "english": False},
        )
        self.page.evaluate(
            """(() => {
              const language = document.querySelector('#language-select');
              language.value = 'en';
              language.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )

        self.page.evaluate(
            "document.querySelector('input[name=workspaceMode][value=dfr]').click()"
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => {
                  const stable = (value) => Array.isArray(value)
                    ? value.map(stable)
                    : value && typeof value === 'object'
                      ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]))
                      : value;
                  return {
                    single: document.querySelector('#dfr-single').textContent,
                    vector: document.querySelector('#dfr-vector').textContent,
                    type: dfrState.snapshot().result.type,
                    requestMatches: JSON.stringify(stable(buildDfrPayload())) === JSON.stringify(stable(
                      window.EASYLATTICE_PREVIEW_FIXTURES.dfr.requests.lwe
                    )),
                    locked: [...document.querySelectorAll('#dfr-form input:not([type=radio]), #dfr-form textarea')]
                      .filter((field) => field.getBoundingClientRect().height > 0)
                      .every((field) => field.readOnly),
                    selectorsInteractive: !document.querySelector('[name=dfrType][value=lwe]').disabled
                      && !document.querySelector('[name=dfrRingType]').disabled,
                  };
                })()"""
            ),
            {
                "single": "-147.14",
                "vector": "-139.14",
                "type": "lwe",
                "requestMatches": True,
                "locked": True,
                "selectorsInteractive": True,
            },
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
            "cyclic": ("x^509 - 1", "0", "1", 509, "-552.23", "-543.24"),
            "negacyclic": ("x^64 + 1", "63", "64", 64, "-34.23", "-33.12"),
            "ntru_prime": ("x^64 - x - 1", "1", "64", 64, "-4.68", "-1.77"),
        }
        ring_failures = {}
        for ring_type, (polynomial, worst, profiles, dimension, single, vector) in expected_rings.items():
            self.page.evaluate(
                f"""(() => {{
                  const select = document.querySelector('[name=dfrRingType]');
                  select.value = '{ring_type}';
                  select.dispatchEvent(new Event('input', {{ bubbles: true }}));
                  select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                  const dimension = document.querySelector('[name=dfrNtruN]');
                  dimension.value = '{dimension + 17}';
                  dimension.dispatchEvent(new Event('input', {{ bubbles: true }}));
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
                  const result = dfrState.snapshot().result;
                  const stable = (value) => Array.isArray(value)
                    ? value.map(stable)
                    : value && typeof value === 'object'
                      ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]))
                      : value;
                  return {
                    single: document.querySelector('#dfr-single').textContent,
                    vector: document.querySelector('#dfr-vector').textContent,
                    ringType: rows['Polynomial ring'],
                    polynomial: rows['Ring polynomial'],
                    worst: rows['Worst coefficient index'],
                    profiles: rows['Distinct coefficient profiles'],
                    dimension: result.dimensions.n,
                    formDimension: Number(document.querySelector('[name=dfrNtruN]').value),
                    requestDimension: buildDfrPayload().n,
                    worstIndex: result.coefficient_dfr.worst_index,
                    failureProbabilities: result.coefficient_dfr.failure_probabilities,
                    singleProbability: result.single_coefficient_failure_probability,
                    vectorProbability: result.vector_failure_probability_before_ecc,
                    precision: rows.Precision,
                    warning: document.querySelector('#dfr-warnings').textContent,
                    invalidText: /\b(undefined|null)\b/.test(document.querySelector('#dfr-results').innerText),
                    requestMatchesFixture: JSON.stringify(stable(buildDfrPayload())) === JSON.stringify(stable(
                      window.EASYLATTICE_PREVIEW_FIXTURES.dfr.requests.ntru[result.ring_type]
                    )),
                    locked: [...document.querySelectorAll('#dfr-form input:not([type=radio]), #dfr-form textarea')]
                      .filter((field) => field.getBoundingClientRect().height > 0)
                      .every((field) => field.readOnly),
                  };
                })()"""
            )
            self.assertEqual(rendered["single"], single)
            self.assertEqual(rendered["vector"], vector)
            self.assertEqual(rendered["ringType"], ring_type)
            self.assertEqual(rendered["polynomial"], polynomial)
            self.assertEqual(rendered["worst"], worst)
            self.assertEqual(rendered["profiles"], profiles)
            self.assertEqual(rendered["dimension"], dimension)
            self.assertEqual(rendered["formDimension"], dimension)
            self.assertEqual(rendered["requestDimension"], dimension)
            self.assertEqual(rendered["precision"], "512 bits (167 decimal digits)")
            failure_strings = rendered["failureProbabilities"]
            ring_failures[ring_type] = failure_strings
            self.assertEqual(len(failure_strings), rendered["dimension"])
            for probability in failure_strings:
                self.assertIsInstance(probability, str)
                self.assertRegex(
                    probability,
                    r"^(?:0|[1-9]\d*)(?:\.\d+)?(?:E[+-]?\d+)?$",
                )
            failures = [Decimal(probability) for probability in failure_strings]
            worst_failure = max(failures)
            self.assertEqual(failures.index(worst_failure), rendered["worstIndex"])
            self.assertEqual(worst_failure, Decimal(rendered["singleProbability"]))
            with localcontext() as context:
                context.prec = 200
                union_probability = min(Decimal(1), sum(failures, Decimal(0)))
                vector_probability = Decimal(rendered["vectorProbability"])
                union_at_vector_precision = union_probability.quantize(
                    Decimal(1).scaleb(vector_probability.as_tuple().exponent)
                )
            self.assertEqual(
                union_at_vector_precision,
                vector_probability,
            )
            self.assertIn("union bound", rendered["warning"])
            self.assertIn("outside this module", rendered["warning"])
            self.assertFalse(rendered["invalidText"])
            self.assertTrue(rendered["requestMatchesFixture"])
            self.assertTrue(rendered["locked"])
            if ring_type == "ntru_prime":
                self.assertIn("makes no independence claim", rendered["warning"])
            self.assert_viewport_layout(1440, 1000)

        self.assertNotEqual(ring_failures["cyclic"], ring_failures["negacyclic"])
        self.assertNotEqual(ring_failures["cyclic"], ring_failures["ntru_prime"])
        self.assertNotEqual(ring_failures["negacyclic"], ring_failures["ntru_prime"])

        self.page.evaluate(
            """(() => {
              const select = document.querySelector('#language-select');
              select.value = 'zh';
              select.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => {
                  const rows = Object.fromEntries([...document.querySelectorAll('#dfr-calculation-list dt')]
                    .map((dt) => [dt.textContent, dt.nextElementSibling.textContent]));
                  return {
                    warning: document.querySelector('#dfr-warnings').textContent.includes('不作独立性假设'),
                    precision: rows['精度'],
                  };
                })()"""
            ),
            {"warning": True, "precision": "512 比特（167 位十进制数字）"},
        )

        self.set_viewport(390, 844, mobile=True)
        self.page.command("Page.reload", {"ignoreCache": True})
        self.page.wait_for(
            "document.readyState === 'complete'"
            " && window.innerWidth === 390"
            " && window.innerHeight === 844"
            " && searchState.snapshot().resultCurrent"
        )
        self.assertEqual(
            self.page.evaluate(
                r"""(() => {
                  const visible = (selector) => {
                    const node = document.querySelector(selector);
                    const rect = node && node.getBoundingClientRect();
                    return Boolean(rect && rect.width > 0 && rect.height > 0);
                  };
                  return {
                    parameterForm: visible('#parameter-form'),
                    searchResults: visible('#search-results'),
                    status: visible('#status-pill'),
                    brand: visible('.brand-row'),
                    language: visible('#language-select'),
                    topStrip: visible('.top-strip'),
                    invalidText: /\b(undefined|null)\b/.test(document.body.innerText),
                  };
                })()"""
            ),
            {
                "parameterForm": True,
                "searchResults": True,
                "status": True,
                "brand": True,
                "language": True,
                "topStrip": True,
                "invalidText": False,
            },
        )
        self.assert_viewport_layout(390, 844)

        self.page.evaluate(
            "document.querySelector('input[name=workspaceMode][value=dfr]').click()"
        )
        self.page.wait_for(
            "dfrState.snapshot().resultCurrent"
            " && dfrState.snapshot().result.type === 'lwe'"
        )
        self.assertTrue(
            self.page.evaluate(
                "document.querySelector('#dfr-results').getBoundingClientRect().height > 0"
            )
        )
        self.assert_viewport_layout(390, 844)

        self.page.evaluate(
            """(() => {
              document.querySelector('input[name=dfrType][value=ntru]').click();
              const ring = document.querySelector('[name=dfrRingType]');
              ring.value = 'ntru_prime';
              ring.dispatchEvent(new Event('input', { bubbles: true }));
              ring.dispatchEvent(new Event('change', { bubbles: true }));
              const dimension = document.querySelector('[name=dfrNtruN]');
              dimension.value = '777';
              dimension.dispatchEvent(new Event('input', { bubbles: true }));
              document.querySelector('#dfr-form button[type=submit]').click();
            })()"""
        )
        self.page.wait_for(
            "dfrState.snapshot().resultCurrent"
            " && dfrState.snapshot().result.ring_type === 'ntru_prime'"
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  form: Number(document.querySelector('[name=dfrNtruN]').value),
                  request: buildDfrPayload().n,
                  result: dfrState.snapshot().result.dimensions.n,
                  invalidText: /\b(undefined|null)\b/.test(
                    document.querySelector('#dfr-results').innerText
                  ),
                }))()"""
            ),
            {"form": 64, "request": 64, "result": 64, "invalidText": False},
        )
        self.assert_viewport_layout(390, 844)

        self.page.evaluate(
            """(() => {
              document.querySelector('input[name=workspaceMode][value=search]').click();
              const result = previewRecommendation({
                hardProblemCategory: 'ntru',
                hardProblemVariant: 'ring',
                ringFamily: 'hps',
                targetSecurity: 140,
                securityModel: 'quantum',
                redCostModel: 'matzov',
                useEstimator: true,
              });
              searchState.setResult(result);
              renderSearchState();
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  status: document.querySelector('#status-pill').textContent,
                  unavailable: document.querySelector('#candidate-list').textContent
                    .includes('量子：无可用安全估计 · 未达到目标'),
                  validationWarning: document.querySelector('#warnings').textContent
                    .includes('运行环境或配置不可用'),
                  quantumWarning: document.querySelector('#warnings').textContent
                    .includes('没有可用的量子安全估计'),
                }))()"""
            ),
            {
                "status": "未达到目标",
                "unavailable": True,
                "validationWarning": True,
                "quantumWarning": True,
            },
        )
        self.assert_viewport_layout(390, 844)

        self.page.evaluate(
            """(() => {
              const request = searchState.begin();
              searchState.acceptError(request, {
                titleKey: 'requestFailed',
                message: 'A deliberately long estimator diagnostic remains visible so operators can inspect the complete failure without losing the previous alternatives.',
              });
              searchState.finish(request);
              renderSearchState();
            })()"""
        )
        self.assertEqual(
            self.page.evaluate(
                """(() => ({
                  status: document.querySelector('#status-pill').textContent,
                  diagnosticVisible: document.querySelector('#summary-subtitle').textContent
                    .includes('complete failure without losing the previous alternatives'),
                  alternativesVisible: document.querySelector('#alternatives')
                    .getBoundingClientRect().height > 0,
                }))()"""
            ),
            {
                "status": "错误",
                "diagnosticVisible": True,
                "alternativesVisible": True,
            },
        )
        self.assert_viewport_layout(390, 844)


if __name__ == "__main__":
    unittest.main()
