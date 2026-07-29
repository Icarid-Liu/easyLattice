# Local Unbounded Estimator Execution Design

## Goal

Make the one-command local checkout work on macOS and let a local Sage
estimator run until it completes, while retaining bounded execution for a
configured remote estimator worker.

## Problems

`start.sh` enables `set -u` and expands an empty `SETUP_ARGS` array. macOS ships
Bash 3.2, where that expansion can fail with `SETUP_ARGS[@]: unbound variable`
when no setup options were supplied.

Estimator timeout semantics are also mixed across execution modes. The browser
sends `estimatorTimeout=240`, treats it as a whole-job deadline, and stops
polling after that deadline. The server applies the same value independently
to local Sage validation attempts. NTRU may attempt several candidates, so the
server can keep running after the browser's deadline. Local estimator attacks
also use per-attack alarms originally intended to bound hosted execution.

The result is inconsistent: local work can be killed despite having no hosting
limit, while a multi-candidate server job may run much longer than the browser
expects and expose only repeated polling requests.

## Execution Policy

The configured estimator mode is the policy boundary:

- When `estimator.remote_url` is absent, execution is local and unbounded.
  Origin preflight, the Sage runner process, individual attacks, the complete
  recommendation job, and browser polling have no elapsed-time deadline.
- When `estimator.remote_url` is present, execution is remote and remains
  bounded by `remote_timeout_seconds`. The existing remote polling interval
  remains in effect.
- Fast-screen requests do not use either estimator policy.

An unbounded local run can still fail when Sage exits, the selected estimator
returns invalid data, or another explicit error occurs. “Unbounded” means that
easyLattice does not terminate it solely because time elapsed.

## Startup Compatibility

`start.sh` will execute `scripts/setup-local.sh --start` directly when
`SETUP_ARGS` is empty. When options exist, it will append the quoted array.
This avoids empty-array expansion under `set -u` and preserves exact argument
boundaries, including paths containing spaces.

Both invocation paths remain foreground `exec` calls, so signals and exit
status continue to belong to the setup/server process.

## Backend Changes

The estimator execution boundary will represent the local timeout as `None`.
Local origin preflight and local Sage execution will pass that value through to
`subprocess.run`, which means no subprocess deadline.

The local runner payload will omit `per_attack_timeout`. The runner will use a
no-op context for attacks when the field is absent and retain the existing
signal alarm only when a positive timeout is supplied. Remote dispatch will
continue to include a bounded per-attack timeout and will continue to use the
remote worker's whole-request timeout.

Recommendation jobs will expose:

- `execution_mode`: `local` or `remote`;
- `elapsed_seconds`: elapsed queue/run time, rounded for display;
- `timeout_seconds`: `null` for local execution and the configured remote
  timeout for remote execution.

These fields are status metadata only and do not alter deterministic parameter
selection.

## Browser Behaviour

The browser already receives `estimator.remote_configured` from public config.
It will use that field rather than a request-level hard-coded timeout:

- local jobs poll until `succeeded`, `failed`, input invalidation, or a network
  error;
- remote jobs stop after the server-advertised timeout plus a small transport
  grace period;
- running status includes elapsed time and explicitly says that local
  estimator execution has no time limit.

Repeated `GET /api/agent/jobs/{id}` requests remain expected two-second status
polling. They will now carry enough status information to distinguish active
local execution from a stuck or expired remote request.

## Failure Handling

Remote timeout failures retain stable error reporting. Local execution does not
create a synthetic timeout failure. A local Sage process that exits or returns
invalid JSON produces the existing estimator failure result.

The browser must not silently convert a remote timeout into a fast-screen
result. It reports the timeout as an estimator job error. Local polling remains
cancellable through the existing input-invalidation path; cancellation stops
browser polling but does not terminate the already-running local computation.
Process cancellation is outside this focused fix.

## Tests

Automated coverage will verify:

- no-option `start.sh` uses the empty-argument-safe invocation;
- setup options remain quoted and forwarded;
- local origin preflight and Sage runner receive no timeout;
- local attack execution does not install an alarm;
- remote execution retains its configured request and attack timeouts;
- local browser jobs poll without an elapsed deadline;
- remote browser jobs remain bounded;
- job JSON reports execution mode, elapsed seconds, and timeout policy.

The complete Python, browser, Node, shell syntax, and patch-format gates must
pass before the changes are pushed to `main`.

## Release

Implementation will be committed on `main` after validation and pushed
directly to `origin/main`, as explicitly requested.
