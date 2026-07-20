# README Configuration, Testing, and Reproducibility Design

## Scope

Update `README.md` and `README.zh.md` only. Do not change startup behavior,
estimator discovery, application code, or machine-specific path candidates.

## Configuration Documentation

Document every estimator setting present in `config.local.example.json`,
including `per_attack_timeout_seconds`. Explain that Sage and the two estimator
source trees may be installed in any directories accessible to the same runtime
environment as easyLattice. Users provide those paths through
`config.local.json` or the existing environment variables; the documentation
must not imply a `~/tookits` or other personal directory convention.

Document the estimator timeout environment variables that correspond to the
configuration fields. Keep the remote-worker and optional LLM examples separate
from local estimator configuration.

## Example Reproducibility

The existing representative result table was generated with estimator
validation disabled. Add an `Estimator commit` column whose value is `not used`
for those rows. Do not attribute deterministic fast-screen values to a
third-party estimator.

Immediately after the table, record the estimator revisions used when optional
validation is enabled:

- standard `malb/lattice-estimator`: `3e48ef42`;
- enhanced `identitymapping/enhanced_lattice-estimator`: `876b6617`.

Use only the first eight hexadecimal characters in the README. Explain that the
applicable commit depends on the selected estimator profile.

## Testing Documentation

Expand the test section in both languages to include:

- the full Python unittest suite;
- the standalone Node model tests;
- Python compilation and JavaScript syntax checks;
- the optional pinned-estimator network smoke test, clearly marked as opt-in and
  dependent on Sage, Git, and network access.

Commands must match paths and environment-variable names that exist in the
repository. English and Chinese instructions must remain structurally aligned.

## Verification

After editing, run Markdown local-link validation, `git diff --check`, the
documented syntax checks, the Node tests, and the Python test suite. The optional
network smoke may remain unexecuted if network or runtime cost makes it
impractical, but its command must be checked against the test source.
