# Browser Estimator Profile and Local Start Design

## Context

The current local UI can read `/api/config/public`, but it cannot configure Sage
or either estimator source tree. `scripts/setup-local.sh` writes configuration
only at startup, and there is no root-level `start.sh`. When the browser submits
`useEstimator=true`, it creates an asynchronous job and polls
`GET /api/agent/jobs/{job_id}`. If the required estimator profile is not
configured, parameter search eventually returns a fast-screen fallback with a
warning. The job itself still succeeds, which makes normal polling look like an
estimator run even though no estimator subprocess was launched.

## Goals

1. Start the local application with `./start.sh` and open the browser when the
   host environment supports it.
2. Configure Sage and estimator source paths from the local browser UI and
   persist them in `config.local.json`.
3. Require a Standard estimator profile and allow an optional Enhanced profile.
4. Keep Standard and Enhanced imports isolated in separate Sage subprocesses.
5. Refuse estimator jobs before enqueueing when the selected algorithm requires
   a missing local profile.
6. Expose meaningful asynchronous job stages so polling is visibly distinct
   from estimator execution.

## Non-Goals

- Do not load both third-party estimator packages into the application process.
- Do not accept arbitrary shell commands or Python source from the browser.
- Do not assume a personal estimator directory such as `~/tookits`.
- Do not install Sage or clone estimator repositories from the browser.
- Do not change the existing remote-estimator worker protocol.

## Startup Flow

A new executable root script, `start.sh`, is the primary local entrypoint. It
delegates setup and server execution to `scripts/setup-local.sh --start`, keeps
the server in the foreground, and preserves `--host`, `--port`, `--force`, and
`--with-estimator` arguments. It also accepts `--no-open`.

Unless `--no-open` is set, a background readiness helper waits for
`/api/health`, then opens the local URL using an available platform launcher.
The launcher order covers WSL, Linux, and macOS. If no launcher succeeds, the
script prints the URL and leaves the server running. A wildcard bind address is
translated to a loopback browser URL.

## Browser Interaction

The live UI adds an estimator-profile settings surface. Preview mode remains
read-only and does not show a writable local profile.

On first live load, if the Standard profile is unavailable, the estimator
configuration dialog opens automatically. The form contains:

- Sage executable, defaulting to `sage` and editable;
- Standard estimator path, required;
- Enhanced estimator path, optional.

The Standard field appears first. Saving validates all non-empty fields. If the
optional Enhanced path fails validation, nothing is written; the user can fix
it or clear it and save Standard alone.

After a successful save, the UI shows each profile's availability, normalized
path, eight-character Git commit, and any validation warning. A visible
"Modify configuration" button remains available and reopens the same dialog.
The current form values are loaded from the backend, not browser local storage.

When `useEstimator` is checked, the UI resolves the profile required by the
selected problem before submission:

- LWE, LWR, and NTRU require Standard;
- RLWE, MLWE, RLWR, and MLWR require Enhanced.

If that local profile is missing, the configuration dialog opens with a focused
message and no job is created. A configured remote estimator worker satisfies
this precondition without local source paths.

## Local Profile API

The local server adds:

```text
GET  /api/config/estimator-profile
POST /api/config/estimator-profile
```

The GET response contains the editable Sage value and separate Standard and
Enhanced profile records. Each profile record has stable fields:

```text
available
path
commit
dirty
error_code
message
```

The POST request accepts only:

```json
{
  "sage_binary": "sage",
  "lattice_estimator_path": "/path/to/malb/lattice-estimator",
  "enhanced_lattice_estimator_path": "/path/to/identitymapping/enhanced-lattice-estimator"
}
```

The Enhanced value may be an empty string or `null`. The Standard value must be
non-empty. Unknown fields, non-string values, oversized values, and non-object
JSON bodies are rejected.

Profile writes target `EASYLATTICE_CONFIG` when it is set; otherwise they target
the repository-local `config.local.json`. This makes tests and custom local
profiles safe without changing the runtime configuration precedence.

The writer parses the existing JSON object, changes only the three estimator
keys above, preserves all timeout, remote-worker, LLM, and scripts settings, and
writes a temporary file in the same directory before `os.replace`. A process
lock serializes concurrent updates. Invalid input never changes the existing
file.

## Path and Import Validation

Path input is trimmed, removes one matching pair of surrounding quotes, and
expands `~`. An estimator path may name either the repository root or its direct
`estimator` package directory; it is normalized to the repository root and must
contain `estimator/__init__.py`.

The Sage value is resolved with `shutil.which` or as an explicit executable
path. Each non-empty estimator profile is then checked in a fresh Sage process:

1. set `PYTHONPATH` to exactly that estimator root plus the application root;
2. set `PYTHONNOUSERSITE=1`;
3. import `estimator`;
4. compare the imported package root to the requested root;
5. return a stable validation result.

The Git commit comes from `git -C <root> rev-parse HEAD` and is exposed as the
first eight characters. A dirty worktree is reported separately. A missing Git
repository does not invalidate an otherwise importable estimator; its commit is
`null` and the profile carries a warning.

This keeps the stronger existing process boundary rather than dynamically
loading Standard and Enhanced into one interpreter. Even when both paths are
configured, each estimator request sees only its selected source tree.

## Server Security Boundary

Writable configuration is local-only. The POST endpoint is enabled only when
the server is bound to a loopback address and the request is same-origin. It
requires `Content-Type: application/json`, uses the existing bounded request
reader, and applies a smaller profile payload limit. CORS never grants access to
the writable endpoint.

The endpoint treats all paths as data. It never accepts command arguments,
environment assignments, Python module names, or script contents from the
browser. Subprocess commands are fixed argument arrays and never use a shell.

## Job Preflight and Progress

`POST /api/agent/jobs` retains the existing asynchronous job protocol. Before a
job is created, the server checks `useEstimator`. For local estimation it
resolves the required profile and returns HTTP 409 with a stable
`estimator_profile_not_configured` error when that profile is unavailable. The
response includes `required_profile` so the UI can open the correct settings
state. Requests that do not ask for estimator validation are unchanged.

Job status retains `queued`, `running`, `succeeded`, and `failed` and adds a
separate stage field:

```text
candidate_search
estimator_running
finalizing
```

The worker installs a request-local progress reporter while executing
`recommend_with_agent`. Candidate generation starts at `candidate_search`.
The centralized estimator process boundary reports `estimator_running` with the
selected profile and its commit immediately before Sage execution. Result
normalization reports `finalizing`. Job JSON includes `stage`,
`estimator_profile`, and `estimator_commit` when known.

The browser continues polling every two seconds, but renders the current stage
and profile rather than a generic waiting message. Final recommendation and
validation contracts remain unchanged.

## Module Boundaries

- `app/local_profile.py`: profile request parsing, path normalization, Sage
  preflight, commit metadata, atomic config persistence, and required-profile
  availability checks.
- `app/job_progress.py`: request-local progress reporter with no dependency on
  HTTP or search modules.
- `app/server.py`: HTTP authorization, routing, status storage, and response
  codes; delegates profile logic to `app.local_profile`.
- `app/estimator_process.py`: calls the shared profile preflight and reports the
  `estimator_running` stage while preserving isolated Sage subprocess execution.
- `static/app.js`: profile dialog orchestration, pre-submit checks, save/modify
  actions, and localized job-stage rendering.
- `static/index.html` and `static/styles.css`: accessible dialog and compact
  status controls consistent with the existing work-focused UI.
- `start.sh`: local startup and browser-opening wrapper.

## Error Contract

Stable profile configuration errors include:

```text
local_configuration_disabled
invalid_profile_request
sage_not_found
sage_not_executable
estimator_path_invalid
estimator_origin_mismatch
estimator_preflight_timeout
estimator_preflight_failed
estimator_profile_not_configured
config_write_failed
```

The UI maps known codes to English and Chinese messages and preserves a safe
backend fallback message for unexpected failures.

## Testing

Backend tests cover request parsing, path normalization, Sage lookup, isolated
origin checks for both profiles, eight-character commits, dirty metadata,
preservation of unrelated configuration, atomic writes, failed-write rollback,
loopback and same-origin enforcement, payload bounds, and 409 job preflight.

Job tests cover stage transitions and ensure the estimator stage is emitted only
when a subprocess is actually attempted. Existing estimator route and origin
tests remain authoritative for Standard/Enhanced isolation.

Browser tests cover first-run dialog opening, Standard-required and
Enhanced-optional saves, profile summaries, the Modify button, missing-profile
submission blocking, remote-worker bypass, stage rendering, and both languages.

An integration test launches `./start.sh --no-open` on a free loopback port,
waits for `/api/health`, and terminates the process. The complete Python, Node,
syntax, and browser suites run before publication.

## Documentation

`README.md` and `README.zh.md` will make `./start.sh` the primary local command,
explain browser-managed profile persistence, retain the manual environment and
JSON alternatives, and state that Enhanced is optional unless a structured LWE
variant is selected.
