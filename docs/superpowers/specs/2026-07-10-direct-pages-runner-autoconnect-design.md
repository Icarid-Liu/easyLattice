# Direct GitHub Pages Runner Auto-Connect Design

## Goal

Let a user open the normal GitHub Pages URL, run a local runner once, fill any
missing Sage/estimator paths, and immediately use the application without
manually setting an API URL or copying a token.

## Connection Flow

The runner listens on the fixed loopback port `8127` by default. The public
browser client attempts a bootstrap request to:

```text
http://127.0.0.1:8127/api/runner/connect
```

The endpoint is unauthenticated only for an exact configured public-page
origin. It returns the loopback API base and the current process token. All
subsequent endpoints still require that token. A foreign origin, a request with
no Origin header, or a non-loopback listener cannot bootstrap a connection.

The UI keeps the token in memory. It removes legacy `apiBase` and `runnerToken`
query parameters from the visible address after it has connected, so copied
public URLs remain clean.

## User States

1. Runner is not running: the public page shows a concise local-runner
   unavailable state and a retry control.
2. Runner is reachable but incomplete: the page prepopulates detected paths and
   shows only the missing path fields.
3. Runner is configured: the page shows `Ready` and starts normal search.

Starting the runner still opens the public page as a convenience, but direct
GitHub Pages visits use the same automatic bootstrap flow.

## Verification

- Unit tests cover bootstrap origin enforcement, response shape, and fixed-port
  defaults.
- Browser verification loads the plain GitHub Pages URL and observes either
  `Paths required` or `Ready` through the loopback runner.
- Existing API token tests continue to require the token for all computation
  endpoints.
