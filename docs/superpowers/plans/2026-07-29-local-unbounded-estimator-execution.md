# Local Unbounded Estimator Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make no-option startup compatible with macOS Bash 3.2, remove all easyLattice elapsed-time limits from local Sage execution, retain bounded remote-worker execution, and report the active policy truthfully in job status and the browser.

**Architecture:** Treat `AppConfig.estimator.remote_url` as the single execution-policy boundary. A local policy passes `None` through origin preflight and Sage subprocess execution and omits per-attack alarms; a remote policy retains `remote_timeout_seconds` and `per_attack_timeout_seconds`. Snapshot that policy when a recommendation job is created, expose it in job JSON, and let the browser derive its polling deadline from the server response instead of a hard-coded request field.

**Tech Stack:** Bash 3.2-compatible shell, Python 3 standard library, Sage subprocesses, vanilla JavaScript, Node's built-in test runner, Python `unittest`, headless Chromium integration tests.

## Global Constraints

- Local execution means `estimator.remote_url` is absent and has no easyLattice elapsed-time deadline at preflight, attack, Sage process, job, or browser-polling layers.
- Remote execution means `estimator.remote_url` is present and remains bounded by `remote_timeout_seconds`.
- Existing configuration fields remain parseable for backward compatibility.
- Parameter selection and estimator result contracts must not change.
- `start.sh` must remain a foreground `exec` wrapper and preserve quoted arguments.
- Work is committed directly on `main` and pushed to `origin/main` only after the complete release gate passes.

---

### Task 1: Make empty startup arguments safe on macOS Bash 3.2

**Files:**
- Modify: `tests/test_start_script.py`
- Modify: `start.sh:157-165`

**Interfaces:**
- Consumes: parsed `SETUP_ARGS` Bash array.
- Produces: an empty-safe foreground invocation of `scripts/setup-local.sh --start`, with the existing quoted forwarding behaviour when options exist.

- [ ] **Step 1: Write the failing regression test**

Add a source-level compatibility assertion to `StartScriptTest`. The existing
functional tests run under the host Bash and cannot reproduce Bash 3.2's empty
array nounset behaviour, so this test locks down the required guarded branch:

```python
    def test_empty_setup_arguments_are_guarded_for_bash_3_2_nounset(self) -> None:
        source = START_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('if [[ "${#SETUP_ARGS[@]}" -eq 0 ]]; then', source)
        self.assertIn(
            'exec "$ROOT_DIR/scripts/setup-local.sh" --start\n'
            "else\n"
            '  exec "$ROOT_DIR/scripts/setup-local.sh" --start "${SETUP_ARGS[@]}"\n'
            "fi",
            source,
        )
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
python3 -m unittest tests.test_start_script.StartScriptTest.test_empty_setup_arguments_are_guarded_for_bash_3_2_nounset -v
```

Expected: `FAIL` because `start.sh` unconditionally expands
`"${SETUP_ARGS[@]}"`.

- [ ] **Step 3: Guard the empty array before expansion**

Replace the final unconditional `exec` in `start.sh` with:

```bash
if [[ "${#SETUP_ARGS[@]}" -eq 0 ]]; then
  exec "$ROOT_DIR/scripts/setup-local.sh" --start
else
  exec "$ROOT_DIR/scripts/setup-local.sh" --start "${SETUP_ARGS[@]}"
fi
```

Do not alter browser startup, URL construction, or option parsing.

- [ ] **Step 4: Run startup tests and shell syntax checks**

Run:

```bash
python3 -m unittest tests.test_start_script -v
bash -n start.sh scripts/setup-local.sh
```

Expected: all startup tests pass and both scripts parse successfully.

- [ ] **Step 5: Commit the startup fix**

```bash
git add start.sh tests/test_start_script.py
git commit -m "Fix no-option startup on macOS Bash"
```

---

### Task 2: Make local Sage preflight and runner processes unbounded

**Files:**
- Modify: `tests/test_agent_config.py`
- Modify: `tests/test_local_profile.py`
- Modify: `app/estimator_process.py:48-134`
- Modify: `app/local_profile.py:261-413`

**Interfaces:**
- Consumes: `run_estimator(payload, timeout, config, profile)` and `profile_record(estimator, profile)`.
- Produces: `run_local_estimator(..., timeout: int | float | None, ...)`, where all normal local callers pass `None`; remote dispatch remains unchanged.

- [ ] **Step 1: Change local-process expectations in the tests**

Update `test_local_attempt_reports_selected_profile_and_commit_before_preflight`
so its preflight callback requires `selected_timeout is None`. Add assertions
for the Sage runner call:

```python
                self.assertIsNone(origin_preflight.call_args.args[1])
                self.assertIsNone(runner.call_args.kwargs["timeout"])
```

Update `test_run_estimator_isolates_and_normalizes_selected_profile_root`:

```python
        self.assertIsNone(preflight_timeout)
        self.assertIsNone(runner_call.kwargs["timeout"])
```

In `tests/test_local_profile.py`, add a profile-state regression around the
existing `profile_record` tests:

```python
    def test_profile_record_uses_unbounded_local_origin_preflight(self) -> None:
        estimator = EstimatorConfig(
            sage_binary="sage-test",
            lattice_estimator_path="/configured",
        )
        runtime = EstimatorRuntime(
            sage_binary="/test/sage",
            root=Path("/configured"),
            environment={},
        )
        with (
            mock.patch(
                "app.local_profile.prepare_estimator_runtime",
                return_value=runtime,
            ),
            mock.patch(
                "app.local_profile.run_origin_preflight",
                return_value={"ok": True},
            ) as preflight,
            mock.patch(
                "app.local_profile.git_metadata",
                return_value=GitMetadata("01234567", False, None),
            ),
        ):
            record = profile_record(estimator, "standard")

        self.assertTrue(record["available"])
        preflight.assert_called_once_with(runtime, None)
```

Use the module's existing imports and temporary paths where required by the
current test class.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
python3 -m unittest \
  tests.test_agent_config.AgentConfigTests.test_local_attempt_reports_selected_profile_and_commit_before_preflight \
  tests.test_agent_config.AgentConfigTests.test_run_estimator_isolates_and_normalizes_selected_profile_root \
  tests.test_local_profile -v
```

Expected: failures show local preflight and runner still receiving numeric
timeouts.

- [ ] **Step 3: Pass `None` at every local process boundary**

In `app/estimator_process.py`, preserve the public `timeout` argument for API
compatibility and remote callers, but make the local dispatch unbounded:

```python
    if config.estimator.remote_url:
        report_progress("estimator_running", profile, None)
        return estimate_remotely(
            base_url=config.estimator.remote_url,
            payload=normalized,
            timeout_seconds=config.estimator.remote_timeout_seconds,
            poll_interval_seconds=config.estimator.remote_poll_interval_seconds,
        )
    return run_local_estimator(normalized, None, config.estimator, profile)
```

Change the local signature:

```python
def run_local_estimator(
    payload: dict[str, Any],
    timeout: int | float | None,
    config: EstimatorConfig,
    profile: str,
) -> dict[str, Any]:
```

Keep passing `timeout` to both subprocess operations:

```python
        preflight_data = run_origin_preflight(runtime, timeout)
        ...
        completed = subprocess.run(
            ...,
            timeout=timeout,
            ...,
        )
```

The value is now `None` for normal local execution, which is the standard
library's explicit no-deadline value. Retain the existing timeout exception
normalization as defensive compatibility for direct callers that invoke
`run_local_estimator` with a number.

In `app/local_profile.py`, broaden the type:

```python
def run_origin_preflight(
    runtime: EstimatorRuntime,
    timeout_seconds: int | float | None,
) -> dict[str, object]:
```

Make profile readiness validation unbounded:

```python
        run_origin_preflight(runtime, None)
```

- [ ] **Step 4: Run the local-process test groups**

Run:

```bash
python3 -m unittest tests.test_agent_config tests.test_local_profile -v
```

Expected: all tests pass; mocks observe `timeout=None` for local execution.

- [ ] **Step 5: Commit the local process policy**

```bash
git add app/estimator_process.py app/local_profile.py \
  tests/test_agent_config.py tests/test_local_profile.py
git commit -m "Remove local Sage process deadlines"
```

---

### Task 3: Disable attack alarms locally while retaining them remotely

**Files:**
- Modify: `tests/test_estimator_runner.py`
- Modify: `tests/test_parameter_search.py`
- Modify: `tests/test_ntru_search.py`
- Modify: `app/estimator_runner.py:20-43,252-406`
- Modify: `app/parameter_search.py:1705-1730`
- Modify: `app/ntru_search.py:840-865`

**Interfaces:**
- Consumes: optional `payload["per_attack_timeout"]`.
- Produces: `time_limit(seconds: int | None)`, where `None` is a no-op context; local request builders omit the field and remote request builders include a positive bounded integer.

- [ ] **Step 1: Add failing runner tests for an absent timeout**

Add the following to `EstimatorRunnerTests`:

```python
    def test_time_limit_none_does_not_install_an_alarm(self) -> None:
        from app.estimator_runner import time_limit

        with (
            patch("app.estimator_runner.signal.signal") as install,
            patch("app.estimator_runner.signal.alarm") as alarm,
        ):
            with time_limit(None):
                pass

        install.assert_not_called()
        alarm.assert_not_called()

    def test_lwe_without_per_attack_timeout_uses_unbounded_contexts(self) -> None:
        payload = self.fake_lwe_payload()
        payload.pop("per_attack_timeout")
        estimator_module = types.ModuleType("estimator")
        estimator_module.LWE = FakeLWE
        estimator_module.ND = FakeND
        models = {
            "matzov": {"classical": "mc", "quantum": "mq"},
            "adps16": {"classical": "ac", "quantum": "aq"},
        }
        with (
            patch.dict(sys.modules, {"estimator": estimator_module}),
            patch("app.estimator_runner.reduction_model_variants", return_value=models),
            patch("app.estimator_runner.cost_to_json", side_effect=fake_cost_to_json),
            patch("app.estimator_runner.estimator_commit", return_value="abc1234"),
            patch("app.estimator_runner.time_limit", wraps=time_limit) as limit,
        ):
            result = run_lwe(payload)

        self.assertTrue(result["ok"])
        self.assertEqual([call.args[0] for call in limit.call_args_list], [None] * 12)
```

Import `time_limit` at module scope or inside the test consistently; there are
three attacks across four model/mode combinations, hence 12 contexts.

- [ ] **Step 2: Add failing request-builder policy tests**

In `tests/test_parameter_search.py`, extend the existing
`test_run_sage_estimator_routes_profiles_and_structured_payload_fields` or add
a focused test:

```python
    def test_lwe_attack_timeout_is_only_sent_to_remote_worker(self) -> None:
        request = RequestOptions(
            hard_problem_category="lwe",
            hard_problem_variant="rlwe",
            use_estimator=True,
        )
        candidate = {
            "ring": {"n": 512},
            "modulus": {"q": 257},
            "distribution": {
                "name": "CBD(2)",
                "secret": {
                    "estimator": {"type": "centered_binomial", "eta": 2}
                },
                "error": {
                    "estimator": {"type": "centered_binomial", "eta": 2}
                },
            },
        }
        local = AppConfig(estimator=EstimatorConfig())
        remote = AppConfig(
            estimator=EstimatorConfig(
                remote_url="https://worker.example",
                per_attack_timeout_seconds=13,
            )
        )
        with patch("app.parameter_search.run_estimator", return_value={"ok": False}) as run:
            run_sage_estimator(candidate, 240, config=local, request=request)
            local_payload = run.call_args.args[0]
            run_sage_estimator(candidate, 240, config=remote, request=request)
            remote_payload = run.call_args.args[0]

        self.assertNotIn("per_attack_timeout", local_payload)
        self.assertEqual(remote_payload["per_attack_timeout"], 13)
```

Add the equivalent assertion to `tests/test_ntru_search.py` around
`run_ntru_estimator`:

```python
    def test_ntru_attack_timeout_is_only_sent_to_remote_worker(self) -> None:
        candidate = recommend_ntru(
            {
                "targetSecurity": 128,
                "ringFamily": "power2",
                "useEstimator": False,
            }
        )["recommendation"]
        request = parse_ntru_request(
            {
                "hardProblemVariant": "ring",
                "ringFamily": "power2",
                "useEstimator": True,
            }
        )
        local = AppConfig(estimator=EstimatorConfig())
        remote = AppConfig(
            estimator=EstimatorConfig(
                remote_url="https://worker.example",
                per_attack_timeout_seconds=13,
            )
        )
        with patch("app.ntru_search.run_estimator", return_value={"ok": False}) as run:
            run_ntru_estimator(candidate, 240, config=local, request=request)
            local_payload = run.call_args.args[0]
            run_ntru_estimator(candidate, 240, config=remote, request=request)
            remote_payload = run.call_args.args[0]

        self.assertNotIn("per_attack_timeout", local_payload)
        self.assertEqual(remote_payload["per_attack_timeout"], 26)
```

The NTRU path intentionally doubles the configured per-attack value and keeps
the existing 5–90 second clamp for remote execution.

- [ ] **Step 3: Run the focused tests and verify they fail**

Run:

```bash
python3 -m unittest \
  tests.test_estimator_runner.EstimatorRunnerTests.test_time_limit_none_does_not_install_an_alarm \
  tests.test_estimator_runner.EstimatorRunnerTests.test_lwe_without_per_attack_timeout_uses_unbounded_contexts \
  tests.test_parameter_search \
  tests.test_ntru_search -v
```

Expected: the runner currently converts an absent timeout to `8`/`20`, and both
request builders currently send a timeout for local execution.

- [ ] **Step 4: Make `time_limit(None)` a no-op**

Move `signal` to a module import so it can be patched consistently:

```python
import signal
```

Replace the context manager with:

```python
@contextmanager
def time_limit(seconds: int | None):
    if seconds is None:
        yield
        return

    def handler(_signum, _frame):
        raise AttackTimeout(f"attack exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
```

In `run_lwe` and `run_ntru`, parse the optional field without inventing a local
default:

```python
    raw_attack_timeout = payload.get("per_attack_timeout")
    per_attack_timeout = (
        int(raw_attack_timeout)
        if raw_attack_timeout is not None
        else None
    )
```

Keep the existing `with time_limit(per_attack_timeout):` call sites.

- [ ] **Step 5: Add per-attack limits only for remote dispatch**

In `app/parameter_search.py`, build the payload without
`per_attack_timeout`, then conditionally add it:

```python
    if config.estimator.remote_url:
        payload["per_attack_timeout"] = max(
            3,
            min(
                90,
                config.estimator.per_attack_timeout_seconds or timeout // 2,
            ),
        )
```

In `app/ntru_search.py`, use:

```python
    if config.estimator.remote_url:
        payload["per_attack_timeout"] = max(
            5,
            min(90, config.estimator.per_attack_timeout_seconds * 2),
        )
```

Do not change route metadata, distributions, or estimator model selection.

- [ ] **Step 6: Run runner and search tests**

Run:

```bash
python3 -m unittest \
  tests.test_estimator_runner \
  tests.test_parameter_search \
  tests.test_ntru_search -v
```

Expected: all tests pass; bounded payloads exist only on the remote path.

- [ ] **Step 7: Commit attack-policy separation**

```bash
git add app/estimator_runner.py app/parameter_search.py app/ntru_search.py \
  tests/test_estimator_runner.py tests/test_parameter_search.py \
  tests/test_ntru_search.py
git commit -m "Keep estimator attack limits remote-only"
```

---

### Task 4: Snapshot and expose recommendation-job execution policy

**Files:**
- Modify: `tests/test_server.py`
- Modify: `app/server.py:1-280,403-480`

**Interfaces:**
- Consumes: one `AppConfig` snapshot returned by `load_config()` at request admission.
- Produces: `RecommendationJob.config`, `execution_mode`, `timeout_seconds`, and JSON `elapsed_seconds`; synchronous and asynchronous recommendation paths use the same admitted config snapshot.

- [ ] **Step 1: Add failing job-policy tests**

Add a focused unit test to `ServerTests`:

```python
    def test_job_json_reports_local_and_remote_execution_policy(self):
        now = 1_000.0
        cases = (
            (
                AppConfig(estimator=EstimatorConfig()),
                "local",
                None,
            ),
            (
                AppConfig(
                    estimator=EstimatorConfig(
                        remote_url="https://worker.example",
                        remote_timeout_seconds=123,
                    )
                ),
                "remote",
                123,
            ),
        )
        for config, mode, timeout in cases:
            with self.subTest(mode=mode), mock.patch(
                "app.server.time.time",
                return_value=now,
            ):
                job = server_module.create_job({"useEstimator": True}, config)
                job.started_at = now - 12.34
                payload = server_module.job_to_json(job)

            self.assertEqual(payload["execution_mode"], mode)
            self.assertEqual(payload["timeout_seconds"], timeout)
            self.assertEqual(payload["elapsed_seconds"], 12.34)
            with server_module.jobs_lock:
                server_module.jobs.pop(job.id, None)
```

Add an admission test showing one config snapshot is reused:

```python
    def test_job_admission_reuses_config_for_preflight_and_execution(self):
        self.clear_jobs()
        config = AppConfig(
            estimator=EstimatorConfig(
                remote_url="https://worker.example",
                remote_timeout_seconds=91,
            )
        )
        request = {"problem": "rlwe", "useEstimator": True}
        try:
            with self.running_server() as server:
                with (
                    mock.patch("app.server.load_config", return_value=config),
                    mock.patch("app.server.require_available_profile") as require,
                    mock.patch("app.server.submit_job") as submit,
                ):
                    response, payload = self.request_json(
                        server,
                        "POST",
                        "/api/agent/jobs",
                        request,
                        {"Content-Type": "application/json"},
                    )

            self.assertEqual(response.status, 202)
            require.assert_called_once_with(request, config=config)
            submitted_job = submit.call_args.args[0]
            self.assertIs(submitted_job.config, config)
            self.assertEqual(payload["execution_mode"], "remote")
            self.assertEqual(payload["timeout_seconds"], 91)
        finally:
            self.clear_jobs()
```

Update `test_run_job_tracks_stages_and_does_not_leak_progress_between_jobs` so
the recommendation mock accepts `config` and assert:

```python
        recommend.assert_called_with(first.payload, config=first.config)
```

- [ ] **Step 2: Run the focused server tests and verify they fail**

Run:

```bash
python3 -m unittest \
  tests.test_server.ServerTests.test_job_json_reports_local_and_remote_execution_policy \
  tests.test_server.ServerTests.test_job_admission_reuses_config_for_preflight_and_execution \
  tests.test_server.ServerTests.test_run_job_tracks_stages_and_does_not_leak_progress_between_jobs -v
```

Expected: `create_job` does not accept a config and job JSON lacks policy and
elapsed fields.

- [ ] **Step 3: Store one admitted config snapshot on each job**

Import config types:

```python
from .config import AppConfig, load_config, public_config
```

Extend `RecommendationJob`:

```python
    execution_mode: str = "local"
    timeout_seconds: int | None = None
    config: AppConfig = field(default_factory=AppConfig, repr=False)
```

Change creation:

```python
def create_job(
    payload: dict[str, Any],
    config: AppConfig | None = None,
) -> RecommendationJob:
    config = config or load_config()
    remote = bool(config.estimator.remote_url)
    job = RecommendationJob(
        id=uuid.uuid4().hex,
        payload=payload,
        execution_mode="remote" if remote else "local",
        timeout_seconds=(
            config.estimator.remote_timeout_seconds if remote else None
        ),
        config=config,
    )
    with jobs_lock:
        jobs[job.id] = job
    return job
```

Run the job with the snapshot:

```python
            result = recommend_with_agent(job.payload, config=job.config)
```

- [ ] **Step 4: Add stable elapsed and policy JSON fields**

At the start of `job_to_json`:

```python
    elapsed_end = job.finished_at if job.finished_at is not None else time.time()
    elapsed_start = job.started_at if job.started_at is not None else job.created_at
```

Add:

```python
        "execution_mode": job.execution_mode,
        "timeout_seconds": job.timeout_seconds,
        "elapsed_seconds": round(max(0.0, elapsed_end - elapsed_start), 2),
```

The finished job's elapsed value stays frozen because it uses `finished_at`.

- [ ] **Step 5: Reuse one config for profile admission and recommendation**

Replace the boolean helper with:

```python
    def recommendation_config(
        self,
        payload: dict[str, Any],
    ) -> AppConfig | None:
        try:
            config = load_config()
            require_available_profile(payload, config=config)
            return config
        except LocalProfileError as exc:
            self.write_json(
                exc.as_api_payload(),
                local_profile_error_status(exc),
            )
        except Exception:
            self.write_json(
                {
                    "ok": False,
                    "code": "config_read_failed",
                    "error": "Could not verify the local estimator configuration.",
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        return None
```

In the job route:

```python
            config = self.recommendation_config(payload)
            if config is None:
                return
            ...
            job = create_job(payload, config)
```

In synchronous recommendation routes:

```python
            config = self.recommendation_config(payload)
            if config is None:
                return
            result = recommend_with_agent(payload, config=config)
```

Update existing server mocks to patch `app.server.load_config` and to accept the
new `config=` keyword where assertions cover exact calls.

- [ ] **Step 6: Run all server tests**

Run:

```bash
python3 -m unittest tests.test_server -v
```

Expected: all server tests pass and each job reports a stable execution policy.

- [ ] **Step 7: Commit job policy metadata**

```bash
git add app/server.py tests/test_server.py
git commit -m "Expose estimator job execution policy"
```

---

### Task 5: Make browser polling local-unbounded and remote-bounded

**Files:**
- Modify: `tests/js/app-model.test.cjs`
- Modify: `tests/test_browser_state.py`
- Modify: `static/app-model.js`
- Modify: `static/app.js:200-235,450-475,760-800,960-1005,1340-1370`

**Interfaces:**
- Consumes: job JSON fields `execution_mode`, `timeout_seconds`, and `elapsed_seconds`.
- Produces: `estimatorJobDeadline(job, nowMs)` returning `null` for local jobs and an absolute deadline for remote jobs; localized truthful running status.

- [ ] **Step 1: Add failing polling-policy unit tests**

Update the exact API export assertion in `tests/js/app-model.test.cjs` to include
`estimatorJobDeadline`. Add:

```javascript
test("local estimator jobs have no browser polling deadline", () => {
  assert.equal(model.estimatorJobDeadline({
    execution_mode: "local",
    timeout_seconds: null,
  }, 10_000), null);
});

test("remote estimator jobs retain a bounded polling deadline", () => {
  assert.equal(model.estimatorJobDeadline({
    execution_mode: "remote",
    timeout_seconds: 123,
  }, 10_000), 163_000);
  assert.equal(model.estimatorJobDeadline({
    execution_mode: "remote",
    timeout_seconds: null,
  }, 10_000), 280_000);
});
```

The remote deadline is `timeout + 30` seconds; a malformed/missing timeout
falls back to the existing 240-second default plus grace.

- [ ] **Step 2: Add a browser payload and status regression**

In `tests/test_browser_state.py`, add a live-page test using the existing fetch
hook:

```python
    def test_local_estimator_job_omits_request_timeout_and_shows_unbounded_status(self):
        self.navigate("")
        self.page.wait_for(
            "document.readyState === 'complete' && window.__requests.length === 1"
        )
        self.page.evaluate(
            """window.__requests[0].resolveResult({
              recommendation: {},
              request: { target_security: 128 },
              validation: { status: 'not_requested' },
              alternatives: [],
              search: {}
            })"""
        )
        self.page.wait_for("searchState.snapshot().inFlight === false")
        self.page.evaluate(
            "document.querySelector('#use-estimator').click();"
            "document.querySelector('#parameter-form').requestSubmit();"
        )
        self.page.wait_for("window.__requests.length === 2")
        payload = self.page.evaluate("window.__requests[1].body")
        self.assertNotIn("estimatorTimeout", payload)
        self.page.evaluate(
            """window.__requests[1].resolveResult({
              ok: true,
              job_id: 'local-job',
              status: 'running',
              stage: 'estimator_running',
              execution_mode: 'local',
              timeout_seconds: null,
              elapsed_seconds: 601.25,
              estimator_profile: 'enhanced',
              estimator_commit: '876b6617'
            }, 202)"""
        )
        self.page.wait_for(
            "document.querySelector('#summary-subtitle').textContent.includes('no time limit')"
        )
```

Adjust selectors to the existing form IDs if the fixture uses a different
checkbox ID. The purpose is to assert both request-field removal and truthful
local status after more than ten elapsed minutes.

- [ ] **Step 3: Run the focused tests and verify they fail**

Run:

```bash
node --test tests/js/app-model.test.cjs
python3 -m unittest \
  tests.test_browser_state.BrowserRequestStateTests.test_local_estimator_job_omits_request_timeout_and_shows_unbounded_status -v
```

Expected: the helper is missing, the request includes `estimatorTimeout`, and
the old subtitle does not identify the unlimited local policy.

- [ ] **Step 4: Implement the pure deadline policy**

In `static/app-model.js` add:

```javascript
  function estimatorJobDeadline(job, nowMs = Date.now()) {
    if (job?.execution_mode !== "remote") return null;
    const configured = Number(job.timeout_seconds);
    const timeoutSeconds = Number.isFinite(configured) && configured > 0
      ? configured
      : 240;
    return nowMs + (timeoutSeconds + 30) * 1000;
  }
```

Export `estimatorJobDeadline` in the returned frozen API object.

- [ ] **Step 5: Remove the request-level hard-coded timeout**

In `requestRecommendation`, remove:

```javascript
    estimatorTimeout: useEstimator ? 240 : undefined,
```

In `requestRecommendationJob`, replace the payload-derived deadline with:

```javascript
  const deadline = EasyLatticeModel.estimatorJobDeadline(submitted);
```

Change the loop condition:

```javascript
  while (deadline === null || Date.now() < deadline) {
```

Remote jobs remain bounded; local jobs exit only on terminal status,
invalidation, or fetch error.

- [ ] **Step 6: Render elapsed time and local unlimited status**

Add English translations:

```javascript
    jobStageEstimatorRunningLocal:
      "Running {profile} estimator{commit} · {elapsed}s elapsed · local execution has no time limit.",
    jobStageEstimatorRunningRemote:
      "Running {profile} estimator{commit} · {elapsed}s elapsed · remote timeout {timeout}s.",
```

Add the Chinese equivalents:

```javascript
    jobStageEstimatorRunningLocal:
      "正在运行 {profile} estimator{commit} · 已运行 {elapsed} 秒 · 本地执行不设时间上限。",
    jobStageEstimatorRunningRemote:
      "正在运行 {profile} estimator{commit} · 已运行 {elapsed} 秒 · 远程超时 {timeout} 秒。",
```

In `estimatorJobMetadata`, specialize `estimator_running`:

```javascript
  const profile = job.estimator_profile === "enhanced" ? "Enhanced" : "Standard";
  if (job.stage === "estimator_running") {
    const local = job.execution_mode !== "remote";
    return {
      subtitleKey: local
        ? "jobStageEstimatorRunningLocal"
        : "jobStageEstimatorRunningRemote",
      subtitleValues: {
        profile,
        commit: job.estimator_commit ? ` @ ${job.estimator_commit}` : "",
        elapsed: Math.max(0, Number(job.elapsed_seconds) || 0).toFixed(1),
        timeout: job.timeout_seconds ?? 240,
      },
    };
  }
```

Keep the existing generic stage mapping for candidate search and finalizing.

- [ ] **Step 7: Run Node and browser tests**

Run:

```bash
node --test tests/js/app-model.test.cjs
python3 -m unittest tests.test_browser_state -v
node --check static/app-model.js
node --check static/app.js
```

Expected: all Node/browser tests and syntax checks pass.

- [ ] **Step 8: Commit browser policy behaviour**

```bash
git add static/app-model.js static/app.js \
  tests/js/app-model.test.cjs tests/test_browser_state.py
git commit -m "Keep local estimator polling unbounded"
```

---

### Task 6: Document the execution-policy boundary and run the release gate

**Files:**
- Modify: `README.md`
- Modify: `README.zh.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Consumes: the implemented local/remote policy and new job JSON fields.
- Produces: clone-ready user instructions and architecture documentation that no longer describe local timeout fields as active limits.

- [ ] **Step 1: Update user documentation**

In both READMEs:

- state that `./start.sh` with no arguments works on macOS Bash 3.2;
- state that local Sage validation has no easyLattice time limit and may run
  for many minutes;
- explain that repeated `GET /api/agent/jobs/{id}` entries are two-second
  status polling, not repeated estimator execution;
- state that remote workers remain bounded by `remote_timeout_seconds`;
- describe `default_timeout_seconds` as retained for configuration
  compatibility rather than an active local deadline;
- describe `per_attack_timeout_seconds` as a remote-worker attack limit.

Document the job response fields:

```json
{
  "status": "running",
  "stage": "estimator_running",
  "execution_mode": "local",
  "timeout_seconds": null,
  "elapsed_seconds": 601.25
}
```

- [ ] **Step 2: Update architecture documentation**

In `docs/architecture.md`, update the estimator subprocess and job sections:

```text
local profile -> no preflight/attack/process/browser deadline
remote profile -> remote_timeout_seconds + remote per-attack policy
```

State that the config snapshot used for admission is stored on the job and
reused by execution, preventing mode/status drift if the local config changes
after submission.

- [ ] **Step 3: Run the focused suites once more**

Run:

```bash
python3 -m unittest \
  tests.test_start_script \
  tests.test_agent_config \
  tests.test_local_profile \
  tests.test_estimator_runner \
  tests.test_parameter_search \
  tests.test_ntru_search \
  tests.test_server \
  tests.test_browser_state -v
node --test tests/js/app-model.test.cjs
```

Expected: all focused Python and Node tests pass.

- [ ] **Step 4: Run the complete release gate**

Run:

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/app-model.test.cjs
python3 -m py_compile app/*.py deploy/huggingface-estimator/space_app.py
bash -n start.sh scripts/setup-local.sh
node --check static/app-model.js
node --check static/app.js
node --check static/preview-data.js
git diff --check
```

Expected: all tests and syntax/format checks pass. The pinned estimator download
smoke may remain skipped unless its opt-in environment variable is set.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md README.zh.md docs/architecture.md
git commit -m "Document local estimator execution policy"
```

- [ ] **Step 6: Verify the exact release scope**

Run:

```bash
git status --short --branch
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
```

Expected: clean `main`; only the approved design, implementation-plan, startup,
execution-policy, browser, test, and documentation commits are ahead of
`origin/main`.

- [ ] **Step 7: Push directly to GitHub main**

Run:

```bash
git push origin main
git status --short --branch
git ls-remote origin refs/heads/main
git rev-parse HEAD
```

Expected: push succeeds, `main` matches `origin/main`, and the two printed commit
hashes are identical.
