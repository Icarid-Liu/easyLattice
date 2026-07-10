# Public Web And Local Runner Companion

## Goal

Enable a publicly hosted easyLattice web UI to use a user's local SageMath and
lattice-estimator installation without cloning this repository or editing
`config.local.json`. The first prototype optimizes for a simple flow: download
and run one local runner, then use the browser page it opens automatically.

## User Flow

1. The user downloads and runs `easyLattice-runner.pyz`.
2. The runner listens only on `127.0.0.1`, detects Sage and common
   lattice-estimator locations, and opens the public web page with its local API
   URL and a temporary connection token.
3. The page shows detected paths and only asks the user to select or enter Sage
   and estimator paths when detection is incomplete.
4. The page submits estimation and DFR requests to the local runner. The paths
   and computations never leave the user's machine.

The zipapp is the prototype distribution format. A later release pipeline can
turn the same runner into native Windows, macOS, and Linux installers.

## Runner API And Runtime Configuration

Add a local-runner entry point that hosts the existing API logic with a
runtime-owned configuration object rather than `config.local.json`.

The runner provides:

- `GET /api/runner/status` for local capability detection and configured paths;
- `POST /api/runner/configure` for Sage and estimator path updates;
- the existing recommendation, asynchronous estimator-job, DFR, health, and
  public-config endpoints.

Configuration updates validate that the Sage path resolves to an executable and
that the estimator root contains `estimator/__init__.py`. The runner passes the
validated in-memory configuration to the existing agent/search functions. It
does not run arbitrary commands, accept arbitrary Python paths, or write a
project-local configuration file.

## Browser Integration

The public interactive page uses relative static asset paths so it works both
when served from the local application and when hosted under a GitHub Pages
repository path. The runner opens the public page with:

`apiBase=http://127.0.0.1:<port>&runnerToken=<token>`.

The frontend sends the token only to the localhost runner through an
`X-EasyLattice-Runner-Token` header. It detects runner status at load time,
prefills detected paths, and exposes a compact connection/settings panel only
when the runner is connected. Existing local-server use remains supported.

## Security Boundary

The runner binds to loopback only and generates a random token at startup. It
accepts authenticated requests only from explicit public UI origins plus local
development origins. CORS permits the token header and JSON requests, but does
not use wildcard origins. State-changing runner configuration and estimator
requests require the token.

The public frontend cannot read arbitrary local files or execute commands. It
can request only the fixed API operations implemented by the runner. Runner
responses report paths and validation status needed by the UI, but never expose
environment secrets.

## Packaging And Verification

Introduce project packaging metadata and a runner build script that produces a
Python zipapp without bundling Sage or lattice-estimator. The runner requires a
local Python runtime for the prototype; Sage and lattice-estimator remain
user-provided installations.

Tests cover path validation, token enforcement, allowed-origin CORS,
configuration isolation, and runner status/configuration responses. A smoke
test starts the runner on a free loopback port, configures test paths, and
executes a non-estimator recommendation request. Frontend checks cover parsing
the runner URL/token and rendering detected-path state.
