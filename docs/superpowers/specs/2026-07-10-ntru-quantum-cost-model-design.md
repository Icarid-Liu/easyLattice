# NTRU Classical and Quantum Cost Model Design

## Goal

Report concrete NTRU classical and quantum bit estimates for the same NTRU
parameters, instead of returning a missing quantum value.

## Estimator Boundary

The existing `NTRU.estimate.rough()` helper is not suitable because it fixes
the reduction model to classical ADPS16. When Sage estimation is requested,
easyLattice will construct one `NTRU.Parameters` instance and call the full
`NTRU.estimate()` API for these four reduction-cost models:

- `MATZOV()`
- `MATZOV(nn="quantum")`
- `ADPS16()`
- `ADPS16(mode="quantum")`

Each run uses the same NTRU parameter object, attack set, shape model, and
per-attack timeout. The response will retain a compatibility `modes` view and
add a `models` mapping by reduction model and computation mode.

## Selection and Presentation

The backend stores MATZOV and ADPS16 classical/quantum values separately. The
selected target, NIST level, metric cards, and margins use the reduction model
chosen by the request. The details panel exposes both model families.

Without `useEstimator=true`, NTRU remains a fast classical reference screen;
it must not invent a quantum number. The UI will state that Sage estimation is
required for the quantum result rather than presenting it as an unclassified
security tier.

## Verification

- Unit tests mock each model/mode result and verify correct selection.
- A live Sage run confirms the configured estimator accepts all four cost
  models for a small NTRU instance.
