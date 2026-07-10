# Log2-First DFR Representation

## Scope

The decryption failure rate calculator will represent DFR primarily as
`log2(DFR)` in its API, UI, and documentation. Raw probabilities remain
available for external error-correction-code calculations and copied JSON, but
are no longer the default DFR representation.

## API

The canonical result fields are:

- `single_coefficient_dfr_log2`
- `vector_dfr_log2_before_ecc`

The raw probability fields are explicit secondary values:

- `single_coefficient_failure_probability`
- `vector_failure_probability_before_ecc`

The existing ambiguous `single_coefficient_dfr` and
`vector_dfr_before_ecc` fields are removed rather than changing their numeric
meaning. This prevents an API consumer from interpreting a log probability as a
probability.

`-Infinity` continues to represent a zero failure probability in the log2
fields.

## UI And Documentation

The DFR result cards, calculation details, and README terminology show only
the log2 fields as DFR. Raw probabilities are not rendered in the UI and remain
available through the Copy JSON button and the API response.

Tests update their assertions to use the explicit probability fields where an
exact probability is needed and the log2 fields where DFR representation is
being checked. Architecture and README documentation describe the log2-first
contract and the retained raw fields for external ECC scripts.
