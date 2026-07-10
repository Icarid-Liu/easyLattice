# Architecture

easyLattice keeps parameter selection deterministic by default. The LLM layer is
an optional front end for translating free-form intent into constraints; it is
not part of the security calculation.

## Layers

1. `app.parameter_search`: deterministic RLWE candidate generation, ranking,
   fast screening, and optional Sage/lattice-estimator validation.
2. `app.ntru_search`: deterministic NTRU candidate generation for power-of-two
   cyclotomic, HPS-like, and HRSS-like instances, with optional
   lattice-estimator NTRU validation with MATZOV/ADPS16 classical and quantum
   reduction-cost models.
3. `app.agent`: orchestration boundary. It always returns the same response
   shape and records whether an LLM was used.
4. `app.llm_provider`: optional OpenAI-compatible chat-completions client. It
   is loaded only when `useLLM=true` and `llm.enabled=true`.
5. `app.decryption_failure`: independent finite-PMF DFR engine for NTRU and
   LWE correctness expressions. It converts estimator-style distribution
   descriptors into finite `value -> probability` maps without modifying the
   third-party estimator.
6. `app.server`: HTTP routing and static UI serving for a local checkout.
7. `static`: browser UI. The LLM checkbox is disabled unless public config says
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

When `useEstimator=true`, the browser submits the same request to
`POST /api/agent/jobs` and polls `GET /api/agent/jobs/{job_id}`. This keeps
3-5 minute Sage/lattice-estimator runs off a single long browser request while
leaving the deterministic fast path synchronous.

## Public Preview and Local Server

GitHub Pages serves `static/preview.html` as a static interface preview. It has
no compute API and does not access local software.

Live interaction starts from a local checkout with:

```bash
./scripts/setup-local.sh --start
```

`app.server` serves `static/index.html` and its API on the same local origin.
The setup script creates `config.local.json`, detects local Sage and
`lattice-estimator` paths when possible, and keeps all paths, estimator output,
and optional API credentials on the user's machine.

## Decryption Failure Path

`POST /api/decryption-failure/calculate` bypasses the agent and estimator. It
runs locally and synchronously:

```text
DFR request JSON -> app.decryption_failure -> finite PMF result JSON
```

The calculator accepts NTRU and LWE forms. It uses `|E| <= Delta` as the
success boundary and returns single-coefficient and pre-error-correction vector
DFR as `log2(DFR)`, using a union bound that does not assume independent output
coefficients. Explicit raw probability fields are retained only for external
ECC calculations.

Bounded estimator-style distributions, custom finite PMFs, LWR floor
compression, and Kyber nearest-integer compression are converted into the same
PMF representation. A generic `ND.NoiseDistribution` has only moments and is
therefore rejected unless the caller supplies a custom PMF. Fixed-weight sparse
ternary is converted to its coefficient marginal with an explicit correlation
warning. The default arithmetic precision is 512 bits; discrete-Gaussian tails
are bounded and reported rather than silently discarded.

Error correction is outside this boundary. Concrete schemes such as LAC and
DAWN must consume the reported pre-correction values in their own ECC model.

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
`targetSecurity`, `ringFamily`, `redCostModel`, `nttScalePower`,
`secretDistribution`, `errorDistribution`, and `compressionP`. Unsupported keys
are dropped before search.

For LWR, RLWR, and MLWR requests, `distribution` is interpreted as the secret
distribution selector for legacy requests. New requests use
`secretDistribution` for `Xs`; `errorDistribution` is a compression modulus
`p`. The rounding-error distribution is generated deterministically as the
centered `q -> p` compression-noise law and is passed to the estimator through a
project-local mediator that creates an `ND.NoiseDistribution` moment profile.

## Secret Handling

`config.local.json` is ignored by git. The public config endpoint never returns
API key values or environment variable names. It only returns whether the key is
present and whether the LLM provider is usable.
