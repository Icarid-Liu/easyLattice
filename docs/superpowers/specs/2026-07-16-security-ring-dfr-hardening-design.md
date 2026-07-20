# Security, Ring, and DFR Hardening Design

## Goal

Improve easyLattice's estimator routing, result-state honesty, NTRU-family
support, polynomial-ring DFR model, secret-distribution validation, bilingual
result rendering, and stale-result handling without modifying either upstream
estimator codebase.

## Scope

This change covers:

- separate standard and enhanced lattice-estimator installations;
- attack routing for LWE, LWR, RLWE, MLWE, RLWR, and MLWR;
- explicit validation and target-satisfaction states;
- Streamlined NTRU Prime candidate presets over `x^n - x - 1`;
- `circulant` NTRU estimation for HPS, HRSS, and NTRU Prime;
- common result fields for RLWE-style and NTRU-style candidates;
- NTRU DFR over cyclic, negacyclic, and NTRU Prime rings;
- estimator-backed secret-distribution comparison;
- `ceil(log2(q))` modulus widths;
- translated dynamic result text and stale-result invalidation;
- focused backend, JavaScript, and browser verification.

The discrete-Gaussian truncation behavior remains unchanged. Vector DFR uses
a union bound. The configured Gaussian tail is considered negligible for this
prototype and remains reported as a warning and separate bound.

The existing shared-variable approximation in the NTRU expression is not
changed by this work. The NTRU Prime coefficient-marginal model described below
adds an explicit correlation warning.

## Estimator Architecture

### Installations

The application supports two independent estimator roots because both projects
export a Python package named `estimator`:

1. `malb/lattice-estimator` is the standard estimator.
2. `identitymapping/enhanced_lattice-estimator` is the enhanced estimator.

They are never imported into the same Python process. The parent process chooses
one root and starts a fresh Sage Python subprocess with that root first on
`PYTHONPATH`.

`EstimatorConfig` gains `enhanced_lattice_estimator_path`. Public configuration
reports the availability and revision of both installations without exposing
private environment-variable names.

`scripts/setup-local.sh --with-estimator` clones both repositories when they are
missing. Existing configurations without the enhanced path continue to run the
fast screen and standard LWE/LWR validation. A structured validation failure is
returned when an enhanced profile is requested but unavailable.

### Routing

The hard-problem variant selects the estimator profile:

| Variant | Estimator profile |
| --- | --- |
| LWE, LWR | standard |
| RLWE, MLWE, RLWR, MLWR | enhanced |
| power2 NTRU, HPS, HRSS, NTRU Prime | NTRU using the configured standard estimator |

HPS, HRSS, and NTRU Prime always use `ntru_type="circulant"`. For power2
NTRU only, the existing matrix/ring control maps to `matrix` or `circulant`.

The estimator payload records `estimator_profile`, `hard_problem_variant`, and
`ring_degree`. The selected estimator revision is returned with every successful
validation.

### LWE-Style Attack Set

Each reduction-cost family is evaluated in classical and quantum modes. The
successful attack with the minimum `rop` determines the security value for that
family and mode.

The standard profile evaluates:

- `LWE.primal_usvp`;
- `LWE.dual_hybrid`;
- `LWE.primal_hybrid` as `bdd_hybrid` with `mitm=False` and `babai=False`.

The enhanced profile evaluates the corresponding functions from
`identitymapping/enhanced_lattice-estimator`:

- `LWE.primal_usvp` as the baseline;
- the fork's `LWE.dual_hybrid`;
- the fork's `LWE.primal_hybrid` as `bdd_hybrid`, with `mitm=False`,
  `babai=False`, `deg_ring=ring_degree`, and `structure_leverage=True`.

For the enhanced quantum `bdd_hybrid`, `Grover=True` is also passed. Classical
and quantum reduction models continue to use the existing MATZOV and ADPS16
model constructors. The enhanced repository documents the ring parameters on
`primal_hybrid`; its `dual_hybrid` is used from the same fork for version
consistency but does not accept `deg_ring`.

Primary upstream reference:
<https://github.com/identitymapping/enhanced_lattice-estimator>

## Distribution Search

Secret and error distributions remain independently enumerated. The fast screen
uses the error distribution for its rough LWE hardness estimate and does not
claim that secret distributions are security-optimal.

When estimator validation is enabled, the validation scheduler rotates across
distinct secret distributions instead of exhausting its budget on adjacent
candidates with the same `Xs`. Security ranking uses estimator results for each
validated `Xs/Xe` pair. Sampling cost and secret standard deviation are only
tie-breakers after security and target satisfaction.

A finite validation budget produces a `partial` validation status and is
described as the best candidate among the validated subset. Only coverage of the
complete eligible distribution pool may be labeled fully validated. The fast
screen alone remains a screening result.

## Result States

Candidate responses add a top-level `validation` object:

```json
{
  "requested": true,
  "status": "validated",
  "profile": "enhanced",
  "estimator_commit": "abcdef0",
  "attempted_candidates": 4,
  "successful_candidates": 4,
  "covered_candidates": 4,
  "eligible_candidates": 16,
  "message_codes": []
}
```

`validation.status` is one of:

- `not_requested`: only the fast screen was requested;
- `validated`: every eligible candidate in the declared search pool was
  successfully validated;
- `partial`: at least one candidate was validated, but the complete pool was not
  covered or some attacks failed;
- `failed`: validation was requested but produced no usable candidate.

The existing `selection` object gains:

```json
{
  "status": "target_met",
  "meets_target": true
}
```

`selection.status` is `target_met` or `target_unmet`. When no candidate reaches
the requested security, the closest candidate may still be returned for
analysis, but the API and UI must clearly report `target_unmet`. A validation
failure may retain a fast-screen candidate, but its source remains fast-screen
and its validation status remains `failed`.

## NTRU Families

The NTRU ring selector exposes:

- power2 NTRU over `x^n + 1`;
- NTRU-HPS over `x^N - 1`;
- NTRU-HRSS over `x^N - 1`;
- Streamlined NTRU Prime over `x^n - x - 1`.

The NTRU Prime family contains the six official Round-3 Streamlined NTRU Prime
parameter sets:

- `sntrup653`;
- `sntrup761`;
- `sntrup857`;
- `sntrup953`;
- `sntrup1013`;
- `sntrup1277`.

Their `n`, `q`, fixed-weight distribution parameters, and reference-screen
metadata come from the official NTRU Prime submission. Live validation passes
the selected parameters to the NTRU estimator as `circulant`.

Primary references:

- <https://ntruprime.cr.yp.to/nist.html>
- <https://ntruprime.cr.yp.to/security.html>

Every NTRU candidate returns the common ring and modulus fields consumed by the
browser. Fields that do not apply, such as a cyclotomic index for NTRU Prime,
are `null` and are omitted by the renderer. The UI never interpolates absent
values into visible text.

## Modulus Width

The displayed and filtered modulus width is `ceil(log2(q))`. For integer
`q >= 2`, the implementation uses `(q - 1).bit_length()`. This makes `q=2048`
an 11-bit modulus and `q=8192` a 13-bit modulus. The same helper is used by
candidate filtering, compactness scoring, API output, and preview fixtures.

## Polynomial-Ring DFR

### Ring Types

NTRU DFR requests gain `ringType` / `ring_type` with these canonical values:

| Value | Polynomial | Intended family |
| --- | --- | --- |
| `cyclic` | `x^n - 1` | HPS, HRSS |
| `negacyclic` | `x^n + 1` | power2 NTRU |
| `ntru_prime` | `x^n - x - 1` | Streamlined NTRU Prime |

The browser defaults NTRU DFR to `cyclic`, matching its HPS example.

### Reduction Profiles

A project-local `app/polynomial_ring.py` module describes how raw product
degrees reduce into each output coefficient. It operates only on coefficient
indices and signs and does not import either estimator.

For `x^n - 1`, `x^n = 1`; every output coefficient has `n` positive product
terms.

For `x^n + 1`, `x^n = -1`; output coefficient `k` has `k+1` direct positive
terms and `n-k-1` wrapped negative terms. Distinct profiles are cached by their
positive and negative multiplicities.

For `x^n - x - 1`, `x^n = x + 1`. Since multiplying two degree-bounded inputs
produces degree at most `2n-2`, one reduction step is sufficient. Raw coefficient
`h_t` contributes as follows:

- `t < n`: to output `t` with sign `+1`;
- `t >= n`: to outputs `t-n` and `t-n+1`, each with sign `+1`.

The NTRU Prime DFR uses a coefficient-marginal approximation: it preserves the
true reduction multiplicities for every output coefficient, but scalar PMF
convolution treats repeated input coefficients as independent. The response
includes a stable warning code for this approximation.

### DFR Aggregation

For each output coefficient, the DFR engine constructs the coefficient PMF for
all enabled NTRU expression terms:

`p0*(g*s) + p1*(f*e) + p2*(f*m) + p3*e`.

Equivalent reduction profiles are cached so large rings do not repeat the same
PMF exponentiation. The existing support and pair-operation budgets still apply.

The response defines:

- `single_coefficient_dfr_log2`: failure probability of the worst output
  coefficient;
- `vector_dfr_log2_before_ecc`: the sum of individual coefficient failure
  probabilities, clamped to one;
- `single_coefficient_semantics`: `worst_coefficient`;
- `coefficient_dfr.worst_index`: index of the worst coefficient;
- `coefficient_dfr.distinct_profiles`: number of cached coefficient profiles;
- `ring_type` and `ring_polynomial`: the normalized ring description.

This vector result is a union bound and does not assume independent output
coefficients. LWE DFR keeps its existing coefficient model and vector union
bound.

## Browser Behavior

The ring-family select is rebuilt when the hard-problem choice changes:

- LWE-style choices offer the existing power2 and ternary cyclotomic forms.
- NTRU offers power2, HPS, HRSS, and NTRU Prime.
- HPS, HRSS, and NTRU Prime force the ring NTRU variant and cannot be submitted
  as matrix NTRU.

Result rendering is field-aware. Optional cyclotomic and NTT rows are omitted
when unavailable. The common fields returned by NTRU and RLWE candidates prevent
visible `undefined` values.

The UI maps `validation.status` and `selection.status` to distinct visible
states. `target_unmet`, `partial`, and `failed` are not displayed as Ready.

Dynamic browser text uses stable codes. The API retains English fallback text
for compatibility and adds warning, source, status, security-level, and next-step
codes. The browser localizes known codes into English or Chinese. Unknown
third-party estimator errors remain visible in their original form.

Changing any effective search or DFR input marks the corresponding result stale:

- the status becomes `inputs_changed`;
- result copy actions are hidden or disabled;
- a response started for an older form revision is ignored when it completes;
- the submit button is disabled while its current request is active.

Switching languages does not mark a result stale because it does not change the
calculation.

## Preview Data

Static preview fixtures follow the same response contract. They include the new
validation and selection states, NTRU optional fields, translated codes, modulus
width convention, and all three NTRU DFR ring descriptions. Preview values remain
illustrative and do not imply live estimator execution.

## Error Handling

- A missing standard or enhanced estimator produces `validation.status=failed`
  with a stable configuration-error code; the server process remains usable.
- An individual attack timeout is recorded under that attack. Other attacks and
  cost modes continue.
- A mode with at least one successful attack has a usable minimum. Missing modes
  produce `partial` rather than discarding all successful estimates.
- Invalid ring types, dimensions, or NTRU Prime presets return HTTP 400.
- A target-unmet search is a successful analytical response, not an HTTP error.
- DFR support-budget failures retain their current explicit validation errors.

## Testing

### Python Unit Tests

- Verify estimator-profile routing for every LWE/LWR variant.
- Mock standard and enhanced estimator APIs and assert the attack set and
  enhanced `deg_ring`, `structure_leverage`, and `Grover` arguments.
- Verify partial model results preserve successful attacks and choose their
  minimum.
- Verify validation and target statuses, including estimator failure and a
  target-unmet NTRU request.
- Verify validation scheduling covers distinct secret distributions before
  repeating a secret profile.
- Verify all six NTRU Prime presets, their polynomial, and `circulant` type.
- Verify HPS and HRSS remain `circulant`.
- Verify `ceil(log2(q))` for powers of two and non-powers of two.
- Compare all three polynomial reduction profiles against brute-force small-ring
  multiplication.
- Verify cyclic, negacyclic, and NTRU Prime DFR on hand-checkable PMFs.
- Verify worst-coefficient and vector-union-bound response fields.

### Browser Tests

Pure state and option-selection functions are moved into a small browser-safe
JavaScript model that can also run under Node's built-in test runner. Tests cover:

- LWE and NTRU ring options;
- HPS/HRSS/NTRU Prime variant forcing;
- validation/target status presentation;
- English and Chinese dynamic-code mapping;
- stale-result revision handling.

### Manual Browser Verification

The shared local and preview UI is checked at desktop and mobile widths. Checks
cover both languages, all NTRU families, target-unmet and validation-failed
states, all three NTRU DFR ring types, copy-button invalidation, absence of
`undefined`, and non-overlapping controls.

The complete Python test suite, JavaScript tests, syntax checks, and diff checks
must pass before implementation is considered complete.
