# Preview UI and WSL Path Repair Design

## Goal

Keep the API-backed browser application and local runner while restoring the
visual language of `static/preview.html`. Accept Windows Explorer UNC paths for
the WSL distribution hosting the runner.

## UI Boundary

`static/preview.html` remains an archived mock: it generates results from
embedded sample data and is not a runner target. `static/index.html` remains
the real client. Its layout and stylesheet will use the preview's compact
control panel, grouped controls, result cards, and restrained green palette,
while retaining existing element IDs, data attributes, language switching,
DFR workspace, and local-runner configuration controls.

The default local-runner URL stays `static/index.html`; no functional endpoint
is moved to the mock preview page.

## WSL Path Boundary

The runner accepts POSIX paths and quoted/unquoted Windows UNC paths in either
form:

- `\\\\wsl.localhost\\<distro>\\path\\to\\file`
- `\\\\wsl$\\<distro>\\path\\to\\file`

When the runner is inside WSL, it converts a UNC path only when `<distro>`
matches `WSL_DISTRO_NAME` case-insensitively. It then validates the resulting
POSIX path exactly as a native input. UNC paths for another distribution are
rejected rather than silently targeting a different filesystem.

## Verification

- Unit tests cover native paths, quoted paths, matching WSL UNC paths, and
  rejected foreign-distribution UNC paths.
- Browser assets remain syntactically valid and the test suite covers runner
  authentication/configuration.
- A live runner verifies the converted Sage and estimator paths through its
  configuration endpoint.
