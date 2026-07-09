# Architecture

easyLattice keeps parameter selection deterministic by default. The LLM layer is
an optional front end for translating free-form intent into constraints; it is
not part of the security calculation.

## Layers

1. `app.parameter_search`: deterministic RLWE candidate generation, ranking,
   fast screening, and optional Sage/lattice-estimator validation.
2. `app.ntru_search`: deterministic NTRU candidate generation for power-of-two
   cyclotomic, HPS-like, and HRSS-like instances, with optional
   lattice-estimator NTRU rough validation.
3. `app.agent`: orchestration boundary. It always returns the same response
   shape and records whether an LLM was used.
4. `app.llm_provider`: optional OpenAI-compatible chat-completions client. It
   is loaded only when `useLLM=true` and `llm.enabled=true`.
5. `app.server`: HTTP routing and static UI serving.
6. `static`: browser UI. The LLM checkbox is disabled unless public config says
   the local LLM provider is enabled and authenticated.

## Default Path

`POST /api/agent/recommend` with no `useLLM` field, or with `useLLM=false`,
runs:

```text
request JSON -> app.agent -> app.parameter_search -> response JSON
```

No network model call is made and no API key is required.

For NTRU, set `"problem": "ntru"`:

```text
request JSON -> app.agent -> app.ntru_search -> response JSON
```

## LLM-Assisted Path

`POST /api/agent/recommend` with `useLLM=true` runs:

```text
intent + current controls
  -> user-owned OpenAI-compatible endpoint
  -> sanitized constraint overrides
  -> app.parameter_search
  -> response JSON
```

The provider may only return a small whitelist of constraint keys such as
`targetSecurity`, `ringFamily`, `redCostModel`, `nttScalePower`, and
`distribution`. Unsupported keys are dropped before search.

For LWR, RLWR, and MLWR requests, `distribution` is interpreted as the secret
distribution selector. The rounding-error distribution is generated
deterministically as a symmetric uniform distribution, and the selected instance
reports the corresponding LWR `p` derived from that support size.

## Secret Handling

`config.local.json` is ignored by git. The public config endpoint never returns
API key values or environment variable names. It only returns whether the key is
present and whether the LLM provider is usable.
