# Unified Adaptive Parameter Search and Distribution Composition

## Status

Design approved in conversation on 2026-08-01. Implementation has not started.

## Problem

RLWE and NTRU currently use different estimator-validation budgets and different
distribution preparation paths. RLWE can return the first estimator-validated
candidate even when it is below the requested security target. NTRU's fast screen
also constructs composite distributions internally, while the parameter-search
UI exposes only single distribution selectors. The result is that identical
search intent is not handled consistently, and a below-target candidate can be
presented as a recommendation.

## Goals

1. Use one adaptive, target-aware search policy for RLWE and NTRU.
2. Select the lexicographically smallest feasible candidate in this order:
   `n` ascending, then `q` ascending, then distribution order.
3. Keep the four estimator comparisons for every completed candidate:
   MATZOV classical/quantum and ADPS16 classical/quantum.
4. Expose independent Secret and Error distribution modules.
5. Support pure distributions and automatically enumerated additive composite
   distributions, with a user-controlled component limit.
6. Preserve local unbounded execution while allowing explicit cancellation of a
   stuck attack.
7. Make below-target and no-feasible-candidate outcomes explicit.

## Non-goals

- This design does not bind the search to a concrete scheme's correctness,
  decryption-failure, encoding, or key-invertibility constraints.
- It does not change the external lattice-estimator repository.
- It does not make a composite moment approximation a proof of sampler or
  security properties.

## User-facing request model

Existing `secretDistribution` and `errorDistribution` selectors remain. Add:

```json
{
  "secretDistributionMode": "pure|combination",
  "errorDistributionMode": "pure|combination",
  "maxDistributionComponents": 3
}
```

The default for both modes is `pure`. The component limit is an integer in the
closed interval 1 through 6 and defaults to 3. A limit of 1 is equivalent to a
pure distribution. Invalid values are rejected by the server.

`pure` generates one selected distribution. `combination` automatically
enumerates unordered sums of 1 through the configured maximum number of
independent components from the supported CBD and sparse-ternary component
families. Secret and Error enumerate independently; their Cartesian product is
then used to build candidates.

For LWR, RLWR, and MLWR, Error is derived compression noise. The UI disables
Error distribution mode/type controls and shows the compression modulus `p`.
The API rejects an explicit Error combination request for these variants rather
than silently changing its meaning.

Each candidate preserves distribution metadata including:

- `family`: `pure` or `composite`;
- `components` and `component_count`;
- mean, variance, standard deviation, and support;
- the estimator representation used for validation.

Composite distributions are passed to the estimator as a conservative
`composite_moment` approximation. Results include an explicit approximation
warning and must not be presented as scheme-level certification.

## Search and selection policy

RLWE and NTRU use the same candidate policy.

1. Enumerate candidates lazily in `n` ascending order.
2. Within each `n`, enumerate NTT-valid `q` in ascending order.
3. Within each `(n, q)`, enumerate the requested distribution candidates in a
   deterministic distribution order. The existing fast distribution ranking is
   retained as the final tie-breaker, with stable pure/composite and component
   metadata tie-breakers.
4. Do not collapse a `(n, q)` group to one distribution before estimator
   validation.
5. Run one estimator request per candidate. The request returns all four model
   and security-mode results.
6. Evaluate the target only with the user's selected security model and
   reduction-cost model.
7. If the selected result is below target, estimator-fails, or is cancelled,
   record the outcome and continue to the next candidate.
8. Return the first candidate that meets the target. Because every preceding
   candidate in the lexicographic order was checked, it is the smallest feasible
   candidate under the user's ordering.
9. If all candidates are exhausted without a target hit, return
   `target_unmet` plus `no_feasible_candidate`. Keep the best checked candidate
   as a reference only; the UI must not title it as a recommendation.

The local mode has no candidate-count or wall-clock limit. Remote execution
retains its existing remote timeout policy. Progress includes eligible and
validated counts, current `n`, `q`, distribution summary, model, mode, and
attack.

## Execution and cancellation

Estimator execution is observable at attack granularity. Each model/mode worker
executes its attacks through terminable Sage subprocesses and emits structured
progress. The local UI offers cancellation of the current attack and of the
whole job.

Cancelling the current attack terminates its subprocess, records `cancelled`,
marks that model/mode incomplete, and continues the remaining attacks, model
modes, and candidates. Cancelling the whole job stops future work and returns
completed results. Local cancellation is explicit; it is not an automatic
timeout. Remote mode continues to enforce its timeout and polling rules.

## UI and labels

Secret and Error appear as independent modules with Pure/Combination controls.
The global component-limit input is visible when combination search is relevant
and is bounded to 1–6.

Cost-model labels and result-table labels use:

- English: `MATZOV (polynomial and sub-exponential terms; more aggressive)`;
  `ADPS16 (exponential term only; more conservative)`.
- Chinese: `MATZOV（考虑多项式及亚指数部分复杂度，更激进）`;
  `ADPS16（只考虑指数部分，更保守）`.

## Errors and result states

- Invalid component limits or malformed mode values produce a clear request
  validation error.
- LWR Error-combination requests are rejected.
- Candidate estimator failures and cancellations are retained in validation
  metadata and do not prevent later candidates from being checked.
- `target_met` means the selected measured model/mode reached the target.
- `target_unmet` means the best checked result did not reach the target.
- `no_feasible_candidate` is emitted only after the candidate iterator is
  exhausted; a bounded/remote interruption is reported as incomplete rather
  than as proof that no feasible candidate exists.

## Testing

### Unit and contract tests

- Parse defaults: both modes are `pure`, component limit is 3.
- Validate limits 1–6 and reject 0, values above 6, non-integers, and malformed
  modes.
- Generate pure CBD and sparse-ternary candidates.
- Generate additive combinations, verify component count, variance, support,
  estimator metadata, and deterministic ordering.
- Verify independent Secret/Error enumeration and LWR Error disabling/rejection.
- Verify estimator mapping for pure and `composite_moment` distributions.
- Verify adaptive search continues after below-target/failure/cancelled
  candidates and stops at the first lexicographically minimal target hit.
- Verify `target_unmet` versus `no_feasible_candidate` semantics.
- Verify MATZOV and ADPS16 labels in English and Chinese.

### Regression fixtures

Use the previously reported NTRU/ring request as a pure-mode fixture:

- target 128, MATZOV classical;
- `x^n + 1`, ring variant;
- centered-binomial Secret and Error;
- q range 7–12 bits;
- NTT `n/2 | q-1`.

Use the same n/q/NTT/distribution constraints with RLWE routing as a second
fixture. Assertions cover routing (`standard` for NTRU and `enhanced` for RLWE),
pure distribution metadata, four comparison fields, adaptive validation, and
explicit target/no-feasible status. Numerical Sage values are not hard-coded in
unit tests.

### Optional live smoke test

An opt-in Sage test runs both fixtures against the configured local estimator,
records the complete JSON shape and status, and is excluded from the default
fast test suite because runtime and estimator revisions vary by machine.

## Implementation boundaries

The implementation should introduce shared distribution/request helpers and a
shared adaptive validation loop, then adapt the RLWE and NTRU candidate
generators to that interface. Existing result fields remain compatible; new
mode, component, progress, cancellation, and no-feasible metadata are additive.
