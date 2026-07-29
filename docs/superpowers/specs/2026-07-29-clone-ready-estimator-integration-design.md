# Clone-Ready Estimator Integration Design

## Context

The repository's default `main` branch currently stops at `ee82d02`, while the
browser-managed estimator profile work is available only on
`codex/browser-estimator-profile` at `2461b4c`. A normal GitHub clone therefore
does not include the local profile dialog, guarded estimator jobs, or the
root-level startup wrapper.

The older public configuration summary also treats an `estimator` package
found through the application interpreter's ambient import path as evidence
that an estimator version exists. It can consequently display:

```text
estimator: <sage> · version <revision> · PYTHONPATH/default
```

even though neither `lattice_estimator_path` nor
`enhanced_lattice_estimator_path` is configured. Local estimator execution
requires an explicit, profile-specific source tree, so an estimator-enabled
request can then fall back to a deterministic fast screen with a configuration
warning. This is misleading because the configuration header appears ready
while the requested estimator profile is unavailable.

This design integrates the existing browser-profile branch and closes the
remaining setup, status, and API gaps before the work is merged into `main`.

## Goals

1. Make the estimator profile workflow available from a normal clone of the
   default branch after merge.
2. Make `./start.sh --with-estimator` the complete fresh-checkout path for users
   who want both local estimator profiles.
3. Preserve existing local configuration while filling profile paths that are
   absent or empty.
4. Treat only an explicit profile that passes its isolated Sage preflight as
   locally ready.
5. Apply the same fail-closed configuration preflight to every recommendation
   endpoint that accepts `useEstimator=true`.
6. Distinguish configuration failures from failures that occur after an
   estimator process was genuinely attempted.
7. Support macOS application paths and repository paths containing spaces
   without shell parsing.

## Non-Goals

- Do not bundle SageMath in this repository.
- Do not vendor either third-party estimator source tree into the main Git
  history or add Git submodules.
- Do not automatically download estimator repositories unless the user passes
  `--with-estimator`.
- Do not overwrite a non-empty user-supplied estimator path during setup.
- Do not change the remote estimator worker protocol.
- Do not turn the deterministic screen into scheme-level security or
  correctness validation.
- Do not enable the optional LLM by default.

## Configuration and Runtime Authority

`config.local.json`, or the file named by `EASYLATTICE_CONFIG`, remains the
file-backed configuration authority. Environment variables retain their
existing precedence over JSON values at runtime.

For local execution, each estimator profile has one explicit source field:

```text
Standard  -> estimator.lattice_estimator_path
Enhanced  -> estimator.enhanced_lattice_estimator_path
```

The fixed request routing remains:

```text
Standard: LWE, LWR, NTRU
Enhanced: RLWE, MLWE, RLWR, MLWR
```

An ambient Python package discovered through `importlib` or `PYTHONPATH` is not
a configured local profile. Ambient discovery may be used by setup only as a
candidate path if it can be resolved to a concrete source root; it must not be
reported as runtime readiness.

A local profile is ready only when all of the following are true:

1. its explicit configured path resolves to a directory containing
   `estimator/__init__.py`;
2. the configured Sage executable resolves to an executable file;
3. a fresh Sage `-python` subprocess imports `estimator` from exactly that
   configured source root;
4. the subprocess completes within the profile preflight timeout.

Standard and Enhanced preflights and estimator runs continue to use separate
Sage subprocesses. Each subprocess receives a `PYTHONPATH` containing only its
selected estimator root and the easyLattice application root, with
`PYTHONNOUSERSITE=1`.

## Startup and Configuration Migration

The documented fresh-checkout workflow is:

```bash
git clone https://github.com/Icarid-Liu/easyLattice.git
cd easyLattice
./start.sh --with-estimator
```

`start.sh` delegates setup and foreground server execution to
`scripts/setup-local.sh`. With `--with-estimator`, setup clones either missing
third-party repository into:

```text
.external/lattice-estimator
.external/enhanced-lattice-estimator
```

Setup then creates or updates the selected local configuration:

- When no configuration file exists, it writes the detected Sage value and
  both detected or cloned estimator paths together with the existing default
  timeout, remote, LLM, and script settings.
- When a configuration file already exists, it fills only an absent, `null`,
  or empty `lattice_estimator_path` or
  `enhanced_lattice_estimator_path`.
- It preserves every non-empty profile path and every unrelated estimator,
  remote-worker, LLM, scripts, and top-level field.
- It writes the updated JSON atomically in the same directory.
- It reports preserved invalid paths but does not silently replace them. The
  user repairs those paths in the browser profile dialog or uses the explicit
  `--force` regeneration option.

Without `--with-estimator`, setup never clones a repository. The live browser
opens the local profile dialog when the required Standard baseline is not
ready, and it opens the dialog focused on Enhanced when an Enhanced-routed
request is selected.

When `estimator.remote_url` is configured, startup and request handling do not
require local Sage or local profile paths. Setup must not overwrite the remote
configuration.

All paths are passed through argument arrays or dedicated environment values.
No path is interpolated into a shell command. A path such as
`/Users/icarid/Desktop/tmp scripts/easyLattice` or a Sage executable inside a
macOS `.app` bundle is therefore treated as one value.

## Public Status and Browser Profile State

`/api/config/public` remains safe for general UI configuration, but its
estimator summary must no longer infer readiness from an ambient package.
It exposes profile-specific, explicit configuration data and remote-worker
state without returning secrets.

The top status area renders one of these truthful states:

```text
remote: configured
Standard: ready | unavailable
Enhanced: ready | unavailable
```

For a ready local profile, the UI may include the normalized path, eight-digit
Git commit, and dirty-worktree marker returned by the guarded local profile
API. For an unavailable profile, it displays a localized reason rather than
`PYTHONPATH/default`. A discovered package revision is never shown as a ready
profile unless that exact source root was explicitly selected and passed the
Sage preflight.

The existing local-only profile dialog remains the editing surface. It saves
only:

```text
sage_binary
lattice_estimator_path
enhanced_lattice_estimator_path
```

and preserves the rest of the configuration. Preview/GitHub Pages mode remains
read-only and does not expose writable profile controls.

## Unified Recommendation Preflight

One backend function resolves the required profile from direct or nested
recommendation payloads. Every local recommendation route calls it before
search or job creation when `useEstimator=true`:

```text
POST /api/agent/jobs
POST /api/agent/recommend
POST /api/rlwe/recommend
```

The preflight is bypassed when estimator validation is not requested or a
remote worker is configured. Unknown or unsupported problem routes retain
their existing request-validation errors and must not be treated as an
available estimator profile.

If the required local profile is unavailable, the endpoint returns HTTP 409
and does not create an asynchronous job, perform candidate search, or return a
fast-screen recommendation. This ensures that browser, direct API, and
compatibility API consumers observe the same contract.

## Error Contract

Pre-execution profile failures use the existing top-level error code:

```json
{
  "ok": false,
  "code": "estimator_profile_not_configured",
  "error": "The enhanced estimator profile is not available.",
  "required_profile": "enhanced",
  "profile_error_code": "estimator_path_invalid"
}
```

`profile_error_code` carries the specific safe cause, including:

```text
sage_not_found
sage_not_executable
estimator_path_invalid
estimator_origin_mismatch
estimator_preflight_timeout
estimator_preflight_failed
```

The UI maps known codes to localized messages and preserves a safe backend
fallback for unknown codes. It must not reduce all failures to the generic
"runtime or configuration is unavailable" warning.

A different contract applies after a profile preflight succeeds and an
estimator execution is genuinely attempted. An estimator timeout, attack
failure, incomplete attack coverage, or invalid estimator result remains part
of the existing recommendation validation contract:

- the deterministic candidate may remain as an explicitly labeled fallback;
- `validation.status` is `failed` or `partial`, as appropriate;
- security source fields continue to identify the deterministic fast screen;
- safe execution diagnostics identify the actual timeout or estimator failure;
- the result never claims estimator validation succeeded.

This preserves useful deterministic output for runtime failures while refusing
to start an estimator-requested calculation when its configuration is missing.

## Component Boundaries

- `scripts/setup-local.sh`: detects or clones source trees and performs
  non-destructive configuration creation/migration.
- `start.sh`: parses startup options, waits for health, opens the browser when
  supported, and keeps the server in the foreground.
- `app/local_profile.py`: owns explicit profile readiness, isolated Sage
  preflight, safe metadata, atomic browser persistence, and request-to-profile
  resolution.
- `app/config.py`: exposes public configuration without treating ambient
  imports as local profile readiness.
- `app/server.py`: applies the unified preflight to synchronous and
  asynchronous recommendation routes and maps stable errors to HTTP responses.
- `app/estimator_process.py`: runs only the selected explicit profile or remote
  worker and reports real execution progress.
- `static/app.js` and `static/app-model.js`: render profile-specific status,
  select required profiles, save local settings, and present localized errors.
- `README.md`, `README.zh.md`, and `docs/architecture.md`: document the
  clone-ready path, profile routing, migration semantics, and failure contract.

No module should independently decide that an ambient `estimator` import is
ready. Readiness and request routing both flow through `app.local_profile`.

## Testing

Backend configuration tests cover:

- absent, `null`, and empty profile paths;
- preservation of non-empty paths and unrelated configuration;
- atomic migration failure without partial file replacement;
- environment-variable precedence;
- remote-worker bypass;
- macOS application paths and repository paths containing spaces;
- exact Standard/Enhanced import origins and isolated subprocess environments.

Server tests submit estimator-enabled payloads to all three recommendation
routes. They verify HTTP 409, `required_profile`, and `profile_error_code` for
missing Standard and Enhanced profiles, and prove that no job or search is
started. Tests also prove that disabled estimator requests and remote-worker
requests retain their current behavior.

Browser and model tests cover:

- truthful Standard and Enhanced status summaries;
- absence of `PYTHONPATH/default` as a ready state;
- first-run and required-profile dialog behavior;
- profile save/modify behavior;
- default RLWE selection requiring Enhanced;
- NTRU, LWE, and LWR requiring Standard;
- localized configuration and post-launch execution failures;
- preview mode remaining read-only.

Startup integration tests use temporary directories and configuration files to
cover a fresh configuration, an existing configuration with empty paths, an
existing configuration with non-empty paths, `--with-estimator`, `--force`,
and `--no-open`. Network cloning is mocked or redirected to local fixture
repositories in the default suite.

The release gate runs:

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

An actual Sage/estimator integration smoke remains opt-in because CI may not
provide Sage or network access. Before merging into `main`, the macOS
reproduction is also run manually with an explicit Sage `.app` executable and
a checkout path containing spaces.

## Integration and Acceptance

Implementation starts from `codex/browser-estimator-profile`, preserving its
already-tested local profile and job progress work, then adds the setup
migration, truthful status, and unified endpoint preflight described here.
The resulting branch is prepared for review and merge into `main`; pushing or
merging remains a separate explicit publication action.

The work is accepted when:

1. a fresh clone can run `./start.sh --with-estimator`, open the local UI, and
   report both cloned profiles accurately;
2. an existing configuration with missing paths is repaired without losing
   other settings;
3. an estimator-enabled request cannot return a configuration-fallback fast
   screen from any supported recommendation endpoint;
4. a real post-launch estimator failure remains explicitly distinguishable
   from missing configuration;
5. the automated release gate passes; and
6. after merge, the same workflow is present on the repository's default
   `main` branch.
