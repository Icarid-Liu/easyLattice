# Decryption Failure Rate Module

## Scope

Add a decryption failure rate (DFR) calculator independent from the lattice
security estimator. It provides NTRU and LWE forms, converts estimator-style
distribution descriptions to finite probability mass functions (PMFs), and
returns the pre-error-correction failure probability. It does not modify
third-party `estimator/nd.py` code or alter the existing security search.

The calculator is intentionally not an error-correction-code model. Schemes
such as LAC and DAWN must consume the reported single-coefficient and
pre-correction vector probabilities in their own correction-probability
scripts.

## Formulas And Boundary

All calculations use the success condition `|E| <= Delta`; a coefficient fails
only when `|E| > Delta`.

For NTRU, the coefficient error distribution is:

`E = p0 * (g*s)_n + p1 * (f*e)_n + p2 * (f*m)_n + p3 * e`.

`(a*b)_n` means the product distribution of one coefficient of `a` and `b`,
summed by `n`-fold convolution. The overall pre-correction DFR is
`1 - (1 - P(|E| > Delta))^n`.

For LWE, the coefficient error distribution is:

`E = ((e1 + ec1)*s)_m + (e*r)_m + e2 + ec2`.

The two product terms use `m`-fold convolution. The output has `n`
coefficients, so the overall pre-correction DFR is
`1 - (1 - P(|E| > Delta))^n`.

The NTRU coefficients `p0`, `p1`, `p2`, `p3`, and `Delta`, and the LWE value
`Delta`, must be non-negative. Numeric values and the restricted expression
`sqrt(2)` are accepted, allowing the NEV coefficient `p1 = sqrt(2)`.

## PMF Engine

Create a project-local finite-PMF module. It owns:

- conversion from estimator-style distribution descriptors to
  `value -> probability` PMFs;
- PMF addition, multiplication, scalar multiplication, and exponentiated
  convolution;
- tail accounting, normalization checks, support-size guards, and result
  serialization.

Probability arithmetic and sample values use at least 512 bits of working
precision (at least 155 decimal significant digits, with a small guard). The
response records the effective precision. A support limit is a hard validation
error: the implementation must never silently discard PMF entries or their
probability mass merely to make a request complete.

Bounded distributions and custom PMFs are expanded exactly up to the selected
working precision. `DiscreteGaussian` is expanded symmetrically using a
configurable `tailBits` bound, defaulting to 128; the response reports an
upper bound on omitted probability mass. A generic `NoiseDistribution` only
contains moments and bounds, not enough information to recover a unique PMF,
so callers must provide it through `custom_pmf`.

`SparseTernary(p, m, n)` is converted to the coefficient marginal:

`{-1: m/n, 0: 1-(p+m)/n, 1: p/n}`.

This matches the supplied reference script, but ignores fixed-weight
cross-coefficient correlation. The response carries an explicit approximation
warning.

## Distribution Inputs

The API and UI support parameterized forms of these common estimator families:

- `centered_binomial`
- `discrete_gaussian`
- `uniform`
- `uniform_mod`
- `t_uniform`
- `sparse_ternary`
- `sparse_binary`
- `binary`
- `ternary`
- `lwr_floor_compression`
- `kyber_nearest_compression`
- `custom_pmf`

`custom_pmf` is a JSON object where each key is a finite sample value and each
value is its non-negative probability. The probabilities must be finite and
sum to one within the configured working precision.

`lwr_floor_compression` uses the existing LWR-style floor definition for
`q -> p`. `kyber_nearest_compression` implements the two nearest-integer
rounding steps used by Kyber, parameterized by modulus `q` and compression-bit
count `d`, so `p = 2^d`. They are separate selectable distributions because
they are not equivalent.

## API

Add a synchronous endpoint:

`POST /api/decryption-failure/calculate`

The request has `type` equal to `ntru` or `lwe`, optional `precisionBits`
(default 512), optional `tailBits` (default 128), and the form-specific
dimensions, coefficients, and distribution descriptors.

NTRU requests contain `n`, `p0`, `p1`, `p2`, `p3`, `delta`, and distributions
for `g`, `f`, `s`, `e`, and `m`. LWE requests contain `m`, `n`, `delta`, and
distributions for `s`, `e`, `e1`, `r`, `e2`, `ec1`, and `ec2`.

The result returns the formula, dimensions, effective precision,
single-coefficient failure probability, vector pre-correction DFR, their
base-2 logarithms, input distribution summaries, any tail bound, and all
approximation warnings. It returns validation errors for unsupported input,
negative coefficients, invalid PMFs, invalid compression parameters, or PMFs
that exceed the support guard.

## UI

Add a separate decryption-failure workspace alongside parameter search, with a
segmented selection between NTRU and LWE. Each distribution editor exposes a
distribution selector and only the matching parameter inputs; the custom
option exposes a PMF JSON editor. NTRU exposes `n`, `p0` through `p3`,
`Delta`, and `g/f/s/e/m`. LWE exposes `m`, `n`, `Delta`, and
`s/e/e1/r/e2/ec1/ec2`.

The result surface shows the formula, single-coefficient error probability,
vector DFR before error correction, binary logarithms, precision, omitted-tail
bound, and warnings. Machine-readable output remains available only through a
copy button; no JSON plaintext is rendered. All new visible text participates
in the existing Chinese/English localization system.

## Verification

Tests cover distribution conversion, both compression modes, custom PMF
validation, exact hand-checkable NTRU and LWE cases, non-negative coefficient
validation, boundary handling at `Delta`, tail-bound reporting, support guards,
and the new API response. A Kyber-512-shaped LWE request verifies the formula
path with `q=3329`, `ec1` compressed to 1024, and `ec2` compressed to 16.

Update the bilingual README and architecture document with the calculator,
its pre-correction-only scope, and the custom PMF requirement for generic
`NoiseDistribution` inputs.
