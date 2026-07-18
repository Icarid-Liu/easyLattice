# Architecture

easyLattice keeps parameter selection deterministic by default. The LLM layer is
an optional front end for translating free-form intent into constraints; it is
not part of the security calculation.

## Layers

1. `app.parameter_search`: deterministic RLWE candidate generation, ranking,
   fast screening, and optional Sage/lattice-estimator validation. It routes
   LWE/LWR to the standard profile and RLWE/MLWE/RLWR/MLWR to the enhanced
   profile.
2. `app.ntru_search`: deterministic NTRU candidate generation for power-of-two
   cyclotomic, HPS-like, HRSS-like, and Streamlined NTRU Prime instances, with
   optional lattice-estimator NTRU validation with MATZOV/ADPS16 classical and
   quantum reduction-cost models through the standard profile.
3. `app.estimator_process`: estimator profile and subprocess boundary. It
   selects the standard or enhanced source tree, launches Sage with an isolated
   `PYTHONPATH` and disabled user site, and verifies the imported `estimator`
   package origin before running `app.estimator_runner`.
4. `app.estimator_contract`: shared per-attack structure-correction metadata
   and coverage rules used by the estimator runner and response validator.
5. `app.security_result`: shared selection and validation result contract,
   including modulus-bit accounting, `target_met`/`target_unmet`, and
   `validated`/`partial`/`failed`/`not_requested` states.
6. `app.agent`: orchestration boundary. It always returns the same response
   shape and records whether an LLM was used.
7. `app.llm_provider`: optional OpenAI-compatible chat-completions client. It
   is imported by `app.agent`, but it is instantiated and invoked only when
   `useLLM=true` and `llm.enabled=true`.
8. `app.polynomial_ring`: exact polynomial multiplication/reduction primitives
   for cyclic `x^n - 1`, negacyclic `x^n + 1`, and NTRU Prime
   `x^n - x - 1` quotient rings.
9. `app.decryption_failure`: independent, ring-aware finite-PMF DFR engine for
   NTRU and LWE correctness expressions. It converts estimator-style
   distribution descriptors into finite `value -> probability` maps without
   modifying the third-party estimator and aggregates vector failure with a
   union bound.
10. `app.server`: HTTP routing and static UI serving for a local checkout.
11. `static/app-model.js`: browser request-state model. Search and DFR have
    independent input revisions and monotonic request tokens. Input changes
    advance the revision, making prior results stale and disabling their
    actions. An identical-input resubmission keeps the revision, so the prior
    result may remain current and copyable while pending, but its new token
    still prevents any older response from winning.
12. `static/app.js`: browser rendering and API orchestration. The LLM checkbox
    is disabled unless public config says the local LLM provider is enabled and
    authenticated.

## Estimator Profile Boundary

The standard and enhanced repositories both import as `estimator`, so they
cannot be selected safely by importing both into the application process.
`app.estimator_process` resolves one configured source root per request,
launches a fresh Sage subprocess, and passes `estimator_profile` in the payload.
The remote worker implements the same profile field and source-origin check.

Routing is fixed: LWE/LWR and NTRU use `standard`; RLWE/MLWE/RLWR/MLWR use
`enhanced`. LWE-family runs evaluate `usvp`, `dual_hybrid`, and `bdd_hybrid`
under MATZOV and ADPS16 classical/quantum models. In the enhanced profile,
both hybrid attacks run in the pinned fork, but explicit ring correction is
available only for `bdd_hybrid`, which receives `deg_ring`,
`structure_leverage=true`, and quantum `Grover=true`. Enhanced `dual_hybrid`
has no explicit ring-structure parameters at that revision. Its finite result
is retained for inspection but excluded from covered-attack ranking.
`app.estimator_contract` emits and validates per-attack `requested`,
`available`, and `applied` booleans plus stable codes/messages. Missing or
forged metadata invalidates the estimator response. Consequently, a structured
response is partial while `dual_hybrid` lacks the requested correction and
cannot become full validation or certify the target from that result alone.
NTRU calls the standard estimator's NTRU attack dispatcher under the same four
reduction-model/mode combinations. For power-of-two NTRU, the requested
security variant maps directly to the estimator: `matrix` becomes
`ntru_type="matrix"`, and `ring` becomes `ntru_type="circulant"`. HPS, HRSS,
and NTRU Prime force the effective security variant to `ring` and
`ntru_type="circulant"`, regardless of a requested `matrix` variant. NTRU
Prime's separate correctness/DFR ring remains `x^n - x - 1`.

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

GitHub Pages serves `static/index.html?preview=1` as a static interface preview.
It uses built-in security and NTRU/LWE DFR fixtures, has no compute API, and
does not access local software.

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
success boundary. Ring-aware NTRU multiplication delegates reduction to
`app.polynomial_ring`; NTRU Prime therefore uses `x^n - x - 1` even though its
security-estimator classification is `circulant`. The response reports the
worst NTRU coefficient marginal (or the identical LWE coefficient model) and
the pre-error-correction vector DFR as `log2(DFR)`. The vector result is the
capped sum of coefficient failure probabilities, a union bound that does not
assume independent output coefficients. Explicit raw probability fields are
retained only for external ECC calculations.

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
