# Browser Estimator Profile and Local Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a local user start easyLattice with `./start.sh`, configure Sage and Standard/Enhanced estimator paths in the browser, persist that profile safely, and see whether an asynchronous job is searching candidates or actually running estimator.

**Architecture:** Add a focused `app.local_profile` service for validation and atomic persistence, and a context-local `app.job_progress` service for worker progress. Keep Standard and Enhanced estimator imports in isolated Sage subprocesses, expose the local profile through guarded HTTP endpoints, preflight estimator jobs before queueing, and add an accessible bilingual settings dialog to the existing UI.

**Tech Stack:** Python 3.10 standard library (`dataclasses`, `contextvars`, `http.server`, `subprocess`, `tempfile`, `ipaddress`), Sage subprocesses, vanilla HTML/CSS/JavaScript, Python `unittest`, Node.js test runner, Chromium CDP browser tests, Bash.

## Global Constraints

- The Standard estimator path is required; the Enhanced estimator path is optional.
- LWE, LWR, and NTRU use Standard; RLWE, MLWE, RLWR, and MLWR use Enhanced.
- Standard and Enhanced must never be imported into the same application process or Sage subprocess.
- Browser writes change only `sage_binary`, `lattice_estimator_path`, and `enhanced_lattice_estimator_path` under the existing `estimator` object.
- `EASYLATTICE_CONFIG` remains the first configuration-file location; otherwise write repository-local `config.local.json`.
- Environment variables retain their existing runtime precedence over JSON settings.
- Profile input values are limited to 4096 UTF-8 characters each; the profile POST body is limited to 16 KiB.
- Writable configuration is enabled only for a loopback-bound server and a same-origin `application/json` request.
- Subprocess commands use fixed argument arrays and `shell=False`; no browser value becomes a command, environment assignment, module name, or source string.
- A remote estimator worker bypasses local profile availability checks and keeps its existing protocol unchanged.
- Existing recommendation and validation result contracts remain unchanged.
- Preview/GitHub Pages mode remains read-only and does not present local profile controls.
- Display Git commits as exactly the first eight hexadecimal characters; report worktree dirtiness separately.
- `start.sh` opens a browser by default, supports `--no-open`, and leaves the server in the foreground.

## File Map

- Create `app/local_profile.py`: profile payload parsing, runtime preparation, isolated import preflight, Git metadata, profile state, atomic persistence, and request-to-profile mapping.
- Create `app/job_progress.py`: context-local progress events, reporter installation, and no-op reporting outside a job.
- Modify `app/estimator_process.py`: consume shared runtime/preflight helpers and report the estimator stage immediately before an actual local or remote estimator attempt.
- Modify `app/server.py`: add profile routes and local-write authorization, estimator job preflight, and progress fields on jobs.
- Modify `app/agent.py`: report candidate-search and finalization stages around deterministic search without changing result data.
- Create `tests/test_local_profile.py`: focused profile parser, validator, metadata, persistence, and availability tests.
- Create `tests/test_job_progress.py`: reporter isolation and event tests.
- Modify `tests/test_agent_config.py`: preserve estimator-process compatibility and prove shared preflight isolation.
- Modify `tests/test_server.py`: HTTP profile security, 409 preflight, and job stage tests.
- Modify `static/app-model.js`: pure required-profile and stage-presentation helpers.
- Modify `tests/js/app-model.test.cjs`: Node tests for the new pure helpers.
- Modify `static/index.html`: live profile status, Modify button, and accessible dialog markup.
- Modify `static/styles.css`: compact settings/status/dialog layout and responsive states.
- Modify `static/app.js`: profile loading/saving, first-run dialog, pre-submit checks, structured API errors, and localized stage rendering.
- Modify `tests/test_browser_state.py`: profile fetch fixtures and browser interaction coverage.
- Create `start.sh`: root startup, readiness polling, browser launch, and foreground server delegation.
- Create `tests/test_start_script.py`: startup integration test.
- Modify `scripts/setup-local.sh`: align help and completion text with browser-managed configuration while retaining setup/clone compatibility.
- Modify `README.md`, `README.zh.md`, and `docs/architecture.md`: document the primary startup flow, profile UI, path semantics, and job stages.

---

### Task 1: Local Profile Validation and Persistence

**Files:**
- Create: `app/local_profile.py`
- Create: `tests/test_local_profile.py`

**Interfaces:**
- Consumes: `AppConfig`, `EstimatorConfig`, `ROOT`, `configured_estimator_source_root`, `load_config`, and `read_json` from `app.config`.
- Produces: `LocalProfileError`, `LocalProfileInput`, `EstimatorRuntime`, `GitMetadata`, `ESTIMATOR_ORIGIN_PREFLIGHT`, `parse_profile_request(payload)`, `prepare_estimator_runtime(estimator, profile)`, `run_origin_preflight(runtime, timeout_seconds)`, `git_metadata(root)`, `profile_record(estimator, profile)`, `local_profile_state(config=None)`, `save_local_profile(payload)`, `required_profile_for_payload(payload)`, and `require_available_profile(payload, config=None)`.

- [ ] **Step 1: Write failing request parsing and path normalization tests**

Create `tests/test_local_profile.py` with table-driven tests covering quoted values, whitespace, `~`, repository-root paths, direct `estimator` package paths, missing Standard, nullable Enhanced, unknown keys, non-string values, NUL bytes, and values over 4096 characters. The core assertions are:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from app.local_profile import LocalProfileError, parse_profile_request


class LocalProfileTests(unittest.TestCase):
    def test_parse_normalizes_repository_and_package_paths(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "estimator"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")

            parsed = parse_profile_request({
                "sage_binary": ' "sage" ',
                "lattice_estimator_path": f'"{package}"',
                "enhanced_lattice_estimator_path": None,
            })

        self.assertEqual(parsed.sage_binary, "sage")
        self.assertEqual(parsed.lattice_estimator_path, str(root.resolve()))
        self.assertIsNone(parsed.enhanced_lattice_estimator_path)

    def test_parse_rejects_unknown_fields_without_normalizing_them(self):
        with self.assertRaises(LocalProfileError) as raised:
            parse_profile_request({
                "sage_binary": "sage",
                "lattice_estimator_path": "/tmp/standard",
                "enhanced_lattice_estimator_path": None,
                "command": "rm -rf /",
            })

        self.assertEqual(raised.exception.code, "invalid_profile_request")
```

- [ ] **Step 2: Run the parser tests and verify the module is missing**

Run:

```bash
python3 -m unittest tests.test_local_profile.LocalProfileTests -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.local_profile'`.

- [ ] **Step 3: Implement stable profile input and error types**

Add these public shapes and constants to `app/local_profile.py`, then implement strict key/type/length checks before path normalization:

```python
PROFILE_FIELDS = frozenset({
    "sage_binary",
    "lattice_estimator_path",
    "enhanced_lattice_estimator_path",
})
PROFILE_VALUE_MAX_CHARS = 4096
STANDARD_VARIANTS = frozenset({"lwe", "lwr"})
ENHANCED_VARIANTS = frozenset({"rlwe", "mlwe", "rlwr", "mlwr"})


class LocalProfileError(ValueError):
    def __init__(self, code: str, message: str, **details: object):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_result(self) -> dict[str, object]:
        return {"ok": False, "code": self.code, "message": self.message, **self.details}

    def as_api_payload(self) -> dict[str, object]:
        return {"ok": False, "code": self.code, "error": self.message, **self.details}


@dataclass(frozen=True)
class LocalProfileInput:
    sage_binary: str
    lattice_estimator_path: str
    enhanced_lattice_estimator_path: str | None


@dataclass(frozen=True)
class EstimatorRuntime:
    sage_binary: str
    root: Path
    environment: dict[str, str]


@dataclass(frozen=True)
class GitMetadata:
    commit: str | None
    dirty: bool | None
    message: str | None
```

Use one helper that trims whitespace, removes exactly one matching surrounding quote pair, rejects NUL, expands `~`, and calls `configured_estimator_source_root`. Reject a path unless the normalized root contains `estimator/__init__.py`.

- [ ] **Step 4: Write failing Sage, origin, commit, and dirty-state tests**

Add tests that patch `shutil.which` and `subprocess.run` to assert the exact command/env contract for both profile names. Include return-code, timeout, non-JSON, import-origin mismatch, clean Git, dirty Git, and non-Git cases. Assert:

```python
self.assertEqual(runtime.environment["PYTHONNOUSERSITE"], "1")
self.assertEqual(
    runtime.environment["PYTHONPATH"].split(os.pathsep),
    [str(runtime.root), str(Path(__file__).resolve().parents[1])],
)
self.assertEqual(record["commit"], "01234567")
self.assertIs(record["dirty"], True)
```

Use the stable codes `sage_not_found`, `sage_not_executable`, `estimator_path_invalid`, `estimator_origin_mismatch`, `estimator_preflight_timeout`, and `estimator_preflight_failed` for the corresponding failures.

- [ ] **Step 5: Implement isolated runtime validation and Git metadata**

Move the existing fixed import script contract into `ESTIMATOR_ORIGIN_PREFLIGHT`. Implement `prepare_estimator_runtime()` so it selects only the requested profile path, resolves Sage with `shutil.which` or an explicit executable file, and constructs a fresh environment with:

```python
environment = os.environ.copy()
environment["PYTHONPATH"] = os.pathsep.join((str(root), str(ROOT)))
environment["PYTHONNOUSERSITE"] = "1"
environment["EASYLATTICE_ESTIMATOR_ROOT"] = str(root)
```

`prepare_estimator_runtime()` accepts `EstimatorConfig`, not `AppConfig`. Implement `run_origin_preflight()` with this fixed command and `shell=False`:

```python
subprocess.run(
    [
        runtime.sage_binary,
        "-python",
        "-c",
        ESTIMATOR_ORIGIN_PREFLIGHT,
        str(runtime.root),
        str(ROOT),
    ],
    text=True,
    capture_output=True,
    timeout=timeout_seconds,
    check=False,
    env=runtime.environment,
)
```

Implement Git metadata with `git -C ROOT rev-parse HEAD` and `git -C ROOT status --porcelain --untracked-files=no`. Return `commit[:8]`, `dirty`, and a non-fatal message when Git metadata is unavailable.

- [ ] **Step 6: Write failing atomic persistence and required-profile tests**

Add tests using `EASYLATTICE_CONFIG` and a temporary JSON file. Prove that a successful write preserves remote timeouts, LLM, scripts, and unrelated top-level data; updates only the three profile keys; uses `os.replace`; and leaves the original bytes unchanged if validation or replacement fails. Cover direct and nested agent payloads:

```python
self.assertEqual(required_profile_for_payload({
    "problem": "rlwe",
    "hardProblemCategory": "lwe",
    "hardProblemVariant": "mlwe",
    "useEstimator": True,
}), "enhanced")
self.assertEqual(required_profile_for_payload({
    "request": {
        "problem": "ntru",
        "hardProblemVariant": "ring",
        "use_estimator": True,
    },
}), "standard")
self.assertIsNone(required_profile_for_payload({"useEstimator": False}))
```

Also prove `require_available_profile()` returns immediately when `remote_url` is set and raises `estimator_profile_not_configured` with `required_profile` for an unavailable local profile.

- [ ] **Step 7: Implement profile state, availability, and atomic writes**

Implement `profile_record()` with this stable record shape for both configured and absent profiles:

```python
{
    "available": bool,
    "path": str | None,
    "commit": str | None,
    "dirty": bool | None,
    "error_code": str | None,
    "message": str | None,
}
```

Implement `local_profile_state()` with `ok`, editable `sage_binary`, `remote_configured`, and `profiles.standard` / `profiles.enhanced`. Implement `save_local_profile()` in this order: parse all input, validate Standard, validate non-empty Enhanced, lock, re-read the target JSON object, update only three estimator keys, write a UTF-8 temporary file in the same directory, flush and `os.fsync`, then `os.replace`. Remove the temporary file on failure and raise `config_write_failed`.

- [ ] **Step 8: Run focused tests and commit**

Run:

```bash
python3 -m unittest tests.test_local_profile -v
python3 -m py_compile app/local_profile.py
git diff --check
```

Expected: all local-profile tests pass and both checks exit zero.

Commit:

```bash
git add app/local_profile.py tests/test_local_profile.py
git commit -m "Add local estimator profile service"
```

---

### Task 2: Context-Local Job Progress and Estimator Process Reuse

**Files:**
- Create: `app/job_progress.py`
- Create: `tests/test_job_progress.py`
- Modify: `app/estimator_process.py`
- Modify: `tests/test_agent_config.py`

**Interfaces:**
- Consumes: `EstimatorRuntime`, `GitMetadata`, `ESTIMATOR_ORIGIN_PREFLIGHT`, `prepare_estimator_runtime`, `run_origin_preflight`, and `git_metadata` from Task 1.
- Produces: `ProgressEvent`, `progress_reporting(reporter)`, `report_progress(stage, estimator_profile=None, estimator_commit=None)`, and an unchanged `run_estimator(payload, timeout, config, profile)` result contract.

- [ ] **Step 1: Write failing context isolation tests**

Create `tests/test_job_progress.py` with tests proving no-op behavior without a reporter, ordered events inside a reporter scope, reset after exceptions, and isolation between copied contexts:

```python
import unittest

from app.job_progress import progress_reporting, report_progress


class JobProgressTests(unittest.TestCase):
    def test_reporter_receives_structured_events_and_resets(self):
        events = []
        with progress_reporting(events.append):
            report_progress("candidate_search")
            report_progress("estimator_running", "standard", "01234567")
        report_progress("finalizing")

        self.assertEqual([event.stage for event in events], [
            "candidate_search",
            "estimator_running",
        ])
        self.assertEqual(events[1].estimator_profile, "standard")
        self.assertEqual(events[1].estimator_commit, "01234567")
```

- [ ] **Step 2: Run the progress test and verify it fails**

Run:

```bash
python3 -m unittest tests.test_job_progress -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.job_progress'`.

- [ ] **Step 3: Implement the request-local reporter**

Implement a frozen event and `ContextVar`-backed context manager:

```python
@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    estimator_profile: str | None = None
    estimator_commit: str | None = None


Reporter = Callable[[ProgressEvent], None]
_REPORTER: ContextVar[Reporter | None] = ContextVar("easyLattice_job_reporter", default=None)


@contextmanager
def progress_reporting(reporter: Reporter) -> Iterator[None]:
    token = _REPORTER.set(reporter)
    try:
        yield
    finally:
        _REPORTER.reset(token)


def report_progress(stage: str, estimator_profile: str | None = None,
                    estimator_commit: str | None = None) -> None:
    reporter = _REPORTER.get()
    if reporter is not None:
        reporter(ProgressEvent(stage, estimator_profile, estimator_commit))
```

- [ ] **Step 4: Write failing estimator-process integration tests**

Extend `tests/test_agent_config.py` to assert that `app.estimator_process` re-exports `ESTIMATOR_ORIGIN_PREFLIGHT` for compatibility, calls the shared runtime/preflight functions, keeps the selected root as the only estimator source, and reports `estimator_running` only after Sage/path validation succeeds. Include these cases:

```text
local Standard attempt -> stage with Standard commit
local Enhanced attempt -> stage with Enhanced commit
missing Sage -> no estimator_running stage
invalid path -> no estimator_running stage
remote worker attempt -> estimator_running with selected profile and null commit
route mismatch -> no estimator_running stage
```

- [ ] **Step 5: Refactor estimator execution onto the shared boundary**

Remove duplicate Sage/path/origin preparation from `app.estimator_process`. Import and re-export the shared preflight constant, prepare one selected runtime, and keep the existing structured failure dictionaries by catching `LocalProfileError`:

```python
try:
    runtime = prepare_estimator_runtime(config.estimator, profile)
except LocalProfileError as exc:
    return exc.as_result()

metadata = git_metadata(runtime.root)
report_progress("estimator_running", profile, metadata.commit)
preflight_data = run_origin_preflight(runtime, timeout)
if not preflight_data.get("ok"):
    return preflight_data
```

For remote execution, report the selected profile immediately before `estimate_remotely()`. Keep the runner command `[sage, "-python", estimator_runner.py]`, input JSON, timeouts, strict JSON decoding, and profile route validation unchanged.

- [ ] **Step 6: Run focused and regression tests, then commit**

Run:

```bash
python3 -m unittest tests.test_job_progress tests.test_agent_config tests.test_estimator_runner -v
python3 -m py_compile app/job_progress.py app/estimator_process.py
git diff --check
```

Expected: all selected tests pass; the opt-in pinned checkout smoke remains skipped unless its environment flag is set.

Commit:

```bash
git add app/job_progress.py app/estimator_process.py tests/test_job_progress.py tests/test_agent_config.py
git commit -m "Report isolated estimator execution progress"
```

---

### Task 3: Guarded Profile HTTP API and Honest Job State

**Files:**
- Modify: `app/agent.py`
- Modify: `app/server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `local_profile_state`, `save_local_profile`, `require_available_profile`, `LocalProfileError`, `ProgressEvent`, and `progress_reporting` from Tasks 1-2.
- Produces: `GET /api/config/estimator-profile`, local-only `POST /api/config/estimator-profile`, HTTP 409 job preflight failures, and job JSON fields `stage`, `estimator_profile`, and `estimator_commit`.

- [ ] **Step 1: Write failing profile endpoint authorization tests**

Add HTTP tests for:

```text
GET returns editable Sage and both stable profile records
POST accepts loopback + same Origin + application/json
POST rejects non-loopback binding with local_configuration_disabled
POST rejects missing or mismatched Origin with local_configuration_disabled
POST rejects non-JSON Content-Type with invalid_profile_request
POST rejects bodies over 16 KiB before reading profile logic
POST maps LocalProfileError code/message/details without exposing a traceback
OPTIONS/CORS never grants a cross-origin writable profile request
```

Use a real temporary `EASYLATTICE_CONFIG` file for one successful POST and mocks for validator-specific failure cases.

- [ ] **Step 2: Run the focused HTTP tests and verify route failures**

Run:

```bash
python3 -m unittest tests.test_server -v
```

Expected: the new endpoint tests fail with HTTP 404 or missing response fields while existing tests pass.

- [ ] **Step 3: Add bounded local profile routes**

Add `PROFILE_MAX_REQUEST_BODY_BYTES = 16_384` and let `request_content_length(maximum=MAX_REQUEST_BODY_BYTES)` accept an endpoint-specific maximum. Add these pure authorization helpers so they can be unit tested independently:

```python
def is_loopback_host(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def same_origin(origin: str, host_header: str) -> bool:
    parsed = urlparse(origin)
    return parsed.scheme == "http" and parsed.netloc == host_header
```

In `do_GET`, delegate `/api/config/estimator-profile` to `local_profile_state()`. In `do_POST`, check server bind address, Origin, Host, Content-Type, and the 16 KiB length before parsing. Return `LocalProfileError.as_api_payload()` with HTTP 400, 403, or 500 according to the stable code. Add an `allow_cors` switch to JSON responses, use `allow_cors=False` for every profile POST response, and make `OPTIONS /api/config/estimator-profile` return no permissive CORS headers.

- [ ] **Step 4: Write failing estimator job preflight and stage tests**

Add tests that assert:

```python
self.assertEqual(response.status, 409)
self.assertEqual(body["code"], "estimator_profile_not_configured")
self.assertEqual(body["required_profile"], "enhanced")
self.assertNotIn(body.get("job_id"), server_module.jobs)
```

Cover Standard and Enhanced requirements, `useEstimator=false`, nested `request`, remote-worker bypass, and invalid problem variants. Add direct `run_job()` tests showing `candidate_search -> estimator_running -> finalizing`, profile/commit retention, failure status, and no stale progress leakage between jobs.

- [ ] **Step 5: Add pre-enqueue checks and context-backed job stages**

Extend `RecommendationJob` without changing its status values. A queued job has no stage until work starts:

```python
stage: str | None = None
estimator_profile: str | None = None
estimator_commit: str | None = None
```

Before `create_job(payload)`, call `require_available_profile(payload)`. Map missing local profiles to HTTP 409 and include `required_profile`; allow non-estimator and remote-worker requests through unchanged.

Wrap `recommend_with_agent()` inside `progress_reporting(update_job_progress)`. The callback updates the three progress fields under `jobs_lock`. In `app.agent.recommend_with_agent`, emit `candidate_search` immediately before `run_deterministic_search()` and `finalizing` immediately after it returns, before agent metadata is attached. Apply this sequence to deterministic and LLM-assisted requests. Extend `job_to_json()` with the three fields while retaining `queued`, `running`, `succeeded`, and `failed`.

- [ ] **Step 6: Run server and configuration regression tests, then commit**

Run:

```bash
python3 -m unittest tests.test_server tests.test_local_profile tests.test_agent_config -v
python3 -m py_compile app/agent.py app/server.py
git diff --check
```

Expected: all selected tests pass, including existing request-size, timeout, keep-alive, CORS, and strict-JSON tests.

Commit:

```bash
git add app/agent.py app/server.py tests/test_server.py
git commit -m "Add guarded profile API and estimator job stages"
```

---

### Task 4: Bilingual Browser Profile Workflow

**Files:**
- Modify: `static/app-model.js`
- Modify: `tests/js/app-model.test.cjs`
- Modify: `static/index.html`
- Modify: `static/styles.css`
- Modify: `static/app.js`
- Modify: `tests/test_browser_state.py`

**Interfaces:**
- Consumes: profile GET/POST and job-stage JSON from Task 3.
- Produces: pure `requiredEstimatorProfile(category, variant)` and `jobStagePresentation(stage)` model helpers; live-only first-run dialog, save/modify actions, profile summaries, pre-submit blocking, and stage-aware polling.

- [ ] **Step 1: Write failing model tests for profile routing and job stages**

Extend the exact export-list assertion and add:

```javascript
test("estimator profiles follow the selected hard problem", () => {
  assert.equal(model.requiredEstimatorProfile("ntru", "ring"), "standard");
  assert.equal(model.requiredEstimatorProfile("lwe", "lwe"), "standard");
  assert.equal(model.requiredEstimatorProfile("lwe", "lwr"), "standard");
  for (const variant of ["rlwe", "mlwe", "rlwr", "mlwr"]) {
    assert.equal(model.requiredEstimatorProfile("lwe", variant), "enhanced");
  }
  assert.equal(model.requiredEstimatorProfile("lwe", "sis"), null);
});

test("job stages map to stable translation keys", () => {
  assert.deepEqual(model.jobStagePresentation("candidate_search"), {
    key: "jobStageCandidateSearch",
    estimatorRunning: false,
  });
  assert.deepEqual(model.jobStagePresentation("estimator_running"), {
    key: "jobStageEstimatorRunning",
    estimatorRunning: true,
  });
  assert.deepEqual(model.jobStagePresentation("finalizing"), {
    key: "jobStageFinalizing",
    estimatorRunning: false,
  });
});
```

- [ ] **Step 2: Implement the pure model helpers and run Node tests**

Implement the exact mappings above without reading DOM or global configuration. Export both functions and run:

```bash
node --test tests/js/app-model.test.cjs
```

Expected: all Node tests pass with the updated exact API list.

- [ ] **Step 3: Add accessible live profile markup and restrained styling**

In `static/index.html`, add a compact estimator profile line beside the existing local configuration summary, a visible `Modify configuration` command button, and one native `<dialog id="estimator-profile-dialog">`. The dialog form contains, in order:

```text
Sage executable
Standard estimator path (required)
Enhanced estimator path (optional)
validation/error message region with role="status"
Cancel and Save buttons
```

Give every input a real `<label>`, use `aria-describedby` for status text, and return focus to the opener on close. Hide the entire writable control when `hasLiveApi()` is false, so `static/preview.html`, `?preview=1`, `file:`, and GitHub Pages remain read-only.

In `static/styles.css`, use the existing neutral palette and button system. Constrain the dialog to `min(42rem, calc(100vw - 2rem))`, use an unframed profile definition list rather than nested cards, keep controls at stable heights, and add mobile wrapping below the existing responsive breakpoint. Include visible focus, loading, available, warning, and error states.

- [ ] **Step 4: Write failing browser tests for first-run, save, modify, and preview**

Update `FETCH_HOOK` so `/api/config/estimator-profile` returns a controlled profile fixture without adding it to `window.__requests`. Default existing tests to an available Standard profile. Add browser tests for:

```text
missing Standard auto-opens dialog and focuses Standard input
Enhanced may be empty and successful save updates both summaries
invalid Enhanced leaves dialog open and preserves entered values
Modify configuration reopens with backend values, not localStorage values
English/Chinese language changes update labels, messages, and button text
preview/GitHub mode has no visible Modify button and never calls profile POST
```

Capture submitted JSON and assert it contains only the three accepted keys.

- [ ] **Step 5: Implement profile loading, saving, and structured API errors**

Add `profileState`, dialog/form DOM references, and focused profile functions to `static/app.js`. The loader and local availability check use this exact flow:

```javascript
async function loadEstimatorProfile() {
  if (!hasLiveApi()) return null;
  profileState = await getJson("/api/config/estimator-profile");
  renderEstimatorProfile(profileState);
  return profileState;
}

function localProfileAvailable(profileName) {
  if (publicConfig?.estimator?.remote_configured) return true;
  return profileState?.profiles?.[profileName]?.available === true;
}

function ensureEstimatorProfile(payload) {
  if (!payload.useEstimator || publicConfig?.estimator?.remote_configured) return true;
  const required = EasyLatticeModel.requiredEstimatorProfile(
    payload.hardProblemCategory,
    payload.hardProblemVariant,
  );
  if (required == null || localProfileAvailable(required)) return true;
  openEstimatorProfileDialog({ requiredProfile: required });
  return false;
}
```

`renderEstimatorProfile()` writes the two profile summaries without using `innerHTML`. `openEstimatorProfileDialog()` loads current backend values, focuses Standard when it is the missing requirement, and records the opener for focus restoration. `closeEstimatorProfileDialog()` closes the native dialog and restores focus. `saveEstimatorProfile(event)` prevents normal submission, posts exactly the three accepted fields, keeps the dialog open on error, and on success refreshes both profile state and `/api/config/public` before closing. On first live load, open the dialog when Standard is unavailable.

Replace message-only HTTP errors with an error object that preserves backend `code`, `required_profile`, and safe fallback text:

```javascript
function apiError(result, fallbackKey) {
  const error = errorWithFallback(result?.error, fallbackKey);
  error.code = result?.code || null;
  error.requiredProfile = result?.required_profile || null;
  return error;
}
```

- [ ] **Step 6: Block missing-profile submissions and render actual job stages**

Build the recommendation payload before calling `searchState.begin()`. If estimator is selected, local mode is active, and the required profile is unavailable, open the dialog with the required profile message and return without creating a request or job. Treat a server-side 409 as a stale-state safeguard that refreshes profile state and opens the same dialog.

Update polling metadata from `job.stage`, `job.estimator_profile`, and `job.estimator_commit` rather than only `job.status`. Add matching English and Chinese strings:

```text
candidate_search: Searching and ranking candidate parameters.
estimator_running: Running {profile} estimator {commit}.
finalizing: Normalizing estimator results and preparing the recommendation.
```

When commit is absent, omit it rather than rendering `null`. Retain two-second polling and the existing terminal job-status handling.

- [ ] **Step 7: Run browser, Node, syntax, and visual checks**

Run:

```bash
node --test tests/js/app-model.test.cjs
node --check static/app-model.js
node --check static/app.js
python3 -m unittest tests.test_browser_state -v
git diff --check
```

Expected: all tests and syntax checks pass. Then start the local service and inspect Chromium screenshots at 1440x1000 and 390x844. Verify the dialog is nonblank, centered without covering its own actions, all long paths wrap or truncate without overlap, focus is visible, and the main interface matches the existing layout after close.

- [ ] **Step 8: Commit the browser workflow**

```bash
git add static/app-model.js static/index.html static/styles.css static/app.js \
  tests/js/app-model.test.cjs tests/test_browser_state.py
git commit -m "Add browser estimator profile workflow"
```

---

### Task 5: One-Command Local Startup

**Files:**
- Create: `start.sh`
- Create: `tests/test_start_script.py`
- Modify: `scripts/setup-local.sh`

**Interfaces:**
- Consumes: `scripts/setup-local.sh --start`, `/api/health`, `HOST`, and `PORT`.
- Produces: executable `./start.sh [--host HOST] [--port PORT] [--force] [--with-estimator] [--no-open]`.

- [ ] **Step 1: Write a failing startup integration test**

Create `tests/test_start_script.py` that selects a free loopback port, sets `EASYLATTICE_CONFIG` to a temporary file, launches `./start.sh --no-open --host 127.0.0.1 --port PORT` in a new process group, polls `/api/health`, asserts `{"ok": true}`, then terminates and waits for the foreground server. Patch the test environment to clear `HOST` and `PORT`, and enforce a 20-second deadline.

Also add a help test asserting `./start.sh --help` documents `--no-open`, `--host`, `--port`, `--force`, and `--with-estimator` without starting a server.

- [ ] **Step 2: Run the startup test and verify the script is missing**

Run:

```bash
python3 -m unittest tests.test_start_script -v
```

Expected: FAIL because `start.sh` does not exist.

- [ ] **Step 3: Implement startup argument forwarding and browser launch**

Create `start.sh` with `set -euo pipefail`. Parse `--no-open` locally, record `--host` and `--port` for the browser URL, and forward every other supported argument unchanged to:

```bash
exec "$ROOT_DIR/scripts/setup-local.sh" --start "${SETUP_ARGS[@]}"
```

Unless `--no-open` is set, start one background readiness helper before `exec`. It waits until `/api/health` succeeds, translates `0.0.0.0`, `::`, and `[::]` to `127.0.0.1` for the browser URL, then tries launchers in this order when available:

```text
wslview
powershell.exe -NoProfile -Command Start-Process
xdg-open
open
```

Use `curl`, `wget`, or a fixed Python `urllib.request.urlopen` fallback for health polling. If no launcher exists or succeeds, print the URL once and leave the foreground server running. Make the file executable.

- [ ] **Step 4: Align setup output with browser-managed paths**

Keep detection, `--force`, `--with-estimator`, smoke testing, and config creation behavior in `scripts/setup-local.sh`. Set `CONFIG_PATH="${EASYLATTICE_CONFIG:-$ROOT_DIR/config.local.json}"` so startup setup and browser persistence target the same file. Change user-facing completion text so an absent estimator is described as configurable in the browser, and so standalone setup recommends `./start.sh` rather than a manual Python command. Do not add personal directory guesses or require estimator command-line arguments.

- [ ] **Step 5: Run shell and integration tests, then commit**

Run:

```bash
bash -n start.sh scripts/setup-local.sh
python3 -m unittest tests.test_start_script -v
git diff --check
```

Expected: syntax checks pass, the help test passes, the integration test reaches `/api/health`, and the child process exits after test termination.

Commit:

```bash
git add start.sh scripts/setup-local.sh tests/test_start_script.py
git commit -m "Add one-command local startup"
```

---

### Task 6: English and Chinese Documentation

**Files:**
- Modify: `README.md`
- Modify: `README.zh.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Consumes: the final startup, profile API, isolation, path, and job-stage behavior from Tasks 1-5.
- Produces: matching user instructions and updated architecture/reference commands.

- [ ] **Step 1: Replace the primary local startup instructions in both READMEs**

Make this the first live-use command in English and Chinese:

```bash
./start.sh
```

State that the browser opens automatically when supported and document:

```bash
./start.sh --no-open
./start.sh --host 127.0.0.1 --port 8003
./start.sh --with-estimator
```

Explain the first-run browser form: Sage defaults to `sage`, Standard is required, Enhanced is optional, successful values are written to `config.local.json`, and Modify configuration remains available.

- [ ] **Step 2: Clarify path and profile semantics**

In both languages, state that paths must be valid in the environment running easyLattice. For WSL service execution, show Linux paths such as `/usr/local/bin/sage` and `/home/user/lattice-estimator`, not a Windows UNC form such as `\\wsl.localhost\Ubuntu-22.04\usr\local\bin\sage`. Preserve the manual JSON and environment-variable alternatives and existing eight-character example commits.

Document the exact routing:

```text
Standard: LWE, LWR, NTRU
Enhanced: RLWE, MLWE, RLWR, MLWR
```

State that a configured remote worker bypasses local paths, and that both local repositories remain isolated in separate Sage subprocesses.

- [ ] **Step 3: Update API, progress, and architecture sections**

Document `GET/POST /api/config/estimator-profile`, local-only write restrictions, and the 409 `estimator_profile_not_configured` response. Update the asynchronous job example to include:

```json
{
  "status": "running",
  "stage": "estimator_running",
  "estimator_profile": "enhanced",
  "estimator_commit": "876b6617"
}
```

Update `docs/architecture.md` to list `app.local_profile`, `app.job_progress`, profile preflight before queueing, and `./start.sh` as the main local entrypoint.

- [ ] **Step 4: Update validation commands and check language parity**

Add `start.sh` and the new tests to both testing sections:

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/app-model.test.cjs
python3 -m py_compile app/*.py deploy/huggingface-estimator/space_app.py
bash -n start.sh scripts/setup-local.sh
node --check static/app-model.js
node --check static/app.js
node --check static/preview-data.js
```

Run:

```bash
rg '^## ' README.md README.zh.md
rg -n 'start.sh|estimator-profile|candidate_search|estimator_running|finalizing' \
  README.md README.zh.md docs/architecture.md
git diff --check
```

Expected: English and Chinese headings remain structurally aligned, each new workflow marker appears in both READMEs, and the diff check exits zero.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md README.zh.md docs/architecture.md
git commit -m "Document browser-managed estimator profiles"
```

---

### Task 7: End-to-End Verification and Final Review

**Files:**
- Modify only files needed to correct failures found by the checks below.

**Interfaces:**
- Consumes: all deliverables from Tasks 1-6.
- Produces: a clean, tested branch with no running verification sessions left behind.

- [ ] **Step 1: Run the complete automated suites**

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

Expected: all default Python and Node tests pass; only explicitly opt-in network/Sage tests may skip; all syntax and diff checks exit zero.

- [ ] **Step 2: Exercise real local profile validation**

Start on a free loopback port with `./start.sh --no-open`. In the browser, enter one installed Sage executable and the actual Standard path; leave Enhanced blank and save. Verify the response shows Standard available, its eight-character commit, and dirty state. Reopen Modify configuration, add the actual Enhanced path, save, and verify both profiles independently report their own commits.

Submit one Standard-profile request and one Enhanced-profile request with estimator enabled. Observe these stages in order:

```text
candidate_search
estimator_running (correct profile and commit)
finalizing
```

Verify server output shows the fixed Sage subprocess command path and does not show repeated polling as estimator invocation. Stop the test server after verification.

- [ ] **Step 3: Verify failure and bypass paths manually**

Temporarily clear Enhanced through Modify configuration, select RLWE, and enable estimator. Verify the dialog opens before a job POST and explains that Enhanced is required. Restore Enhanced, then test a deliberately invalid path and verify save fails without changing the persisted file. If a remote worker URL is available in local configuration, verify the same RLWE submission bypasses local path checks without changing the worker protocol.

- [ ] **Step 4: Inspect desktop/mobile and preview rendering**

Capture live screenshots at 1440x1000 and 390x844 with the dialog open and closed. Also open `static/preview.html` and the GitHub Pages-compatible static route. Confirm:

```text
no overlap or horizontal overflow
long paths do not resize controls
dialog actions remain visible
keyboard focus order is coherent
English and Chinese labels fit
Modify configuration is absent from preview mode
security and DFR workspaces still render normally
```

- [ ] **Step 5: Review the final diff and commit only verification fixes**

Run:

```bash
git status --short
git diff --stat
git diff --check
git log --oneline --decorate -7
```

Expected: only intended files are changed, no generated profile/config files are staged, and the task commits are present. If verification required corrections, commit only those corrections:

```bash
git add app static tests scripts start.sh README.md README.zh.md docs/architecture.md
git commit -m "Fix estimator profile integration regressions"
```

If no correction was needed, do not create an empty commit.
