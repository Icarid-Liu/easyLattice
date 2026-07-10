# Split Distribution Search, LWR Compression Noise, and Localization

## Scope

The parameter assistant will expose separate secret and error distribution choices, add a Chinese/English language toggle, and label selected security bits with NIST-style levels.

## Search Behavior

For LWE-style variants other than LWR/RLWR/MLWR, the request accepts `secretDistribution` and `errorDistribution`. Each field may be `auto`, `centered_binomial`, or `sparse_ternary`. The backend searches the Cartesian product of enabled secret and error candidates, while the legacy `distribution` field remains a compatibility fallback.

For LWR-style variants, the secret distribution uses the same selector, but the error selector is a compression modulus `p`. The rounding error is generated from `q` and `p` with the compression-noise law:

`floor(floor(vi * p / q) * q / p) - vi`, balanced modulo `q`, for `vi` in `{0, 1, ..., q-1}`.

The project will keep this as an internal mediator module and will not modify third-party lattice-estimator code. The estimator adapter will translate the generated moment profile into `ND.NoiseDistribution`.

## UI

The form will show separate controls for secret and error distributions. When an LWR-style variant is selected, the error control switches to compression modulus `p` choices. The result view will show `Xs` and `Xe` separately.

The language toggle will translate static labels, status messages, common dynamic labels, and button text through a local dictionary. The selected language persists in `localStorage`.

## Security Level

The backend will classify selected security bits as:

- below 128: below NIST-I
- 128 to below 192: NIST-I
- 192 to below 256: NIST-III
- 256 and above: NIST-V

The result view will display the level near the selected metric and in the estimate details.
