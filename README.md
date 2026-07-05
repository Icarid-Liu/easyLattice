# AILattice

Local-first, open-source prototype for AI-assisted lattice-crypto parameter
selection.

This first version focuses on a basic RLWE instance selector:

- power-of-two cyclotomic ring `Z_q[x] / (x^n + 1)`;
- ternary cyclotomic ring `Z_q[x] / (x^n - x^(n/2) + 1)` for even `n` whose
  prime factors are only `2` and `3`;
- NTT-friendly prime modulus with `n | q - 1`; full splitting
  `2n | q - 1` is preferred, but leaving one NTT layer unresolved is treated as
  nearly as good;
- centered binomial secret/error distributions;
- iid sparse ternary secret/error distributions with fixed-weight estimator
  approximation;
- fast local screening plus optional user-provided Sage/lattice-estimator validation;
- a small web UI for interactive parameter search.

The selector treats the user's requested security as a lower bound. It first
chooses the polynomial/ring family, then degree `n`, then the smallest modulus
satisfying the chosen NTT scale, and only then chooses the secret/error
distribution for that modulus. Within a fixed modulus it still avoids
unnecessary security margin.

The JSON output already separates `secret` and `error` distribution fields. The
current prototype searches paired `Xs = Xe` distributions; independent `Xs, Xe`
search is the next natural extension once scheme-specific correctness scripts
are added.

The basic monotonicity heuristic remembered by the selector is: smaller `q`
usually increases LWE/RLWE hardness, larger dimension increases hardness, and
larger error standard deviation increases hardness. Correctness and scheme
encoding may push in the opposite direction, so those checks belong in
scheme-specific modules.

For sparse ternary candidates, AILattice includes distributions with
`Pr[+1] = Pr[-1] = (2^l0 - 1) / 2^(2*l0 + l1)` and all remaining probability on
`0`. These are cheap to sample with bit operations. Since `lattice-estimator`
models sparse ternary vectors by fixed Hamming weight, AILattice passes the
expected `+1` and `-1` counts as a fixed-weight approximation and reports that
approximation in the JSON output.

AILattice is designed as a local tool, not a hosted service. Users bring their
own model endpoint/API key, their own estimator installation, and later their
own scheme-specific scripts such as decryption-error or smoothing-parameter
calculators.

No API key is required for the current RLWE prototype. Model configuration is
present only so the agent layer can be added without committing secrets or
depending on the maintainer's tokens.

## Run

```bash
python3 -m app.server
```

Then open:

```text
http://127.0.0.1:8000
```

Use another port if needed:

```bash
PORT=8010 python3 -m app.server
```

## Local Configuration

Copy the example file and edit local paths:

```bash
cp config.local.example.json config.local.json
```

`config.local.json` is ignored by git. It can contain:

- `estimator.sage_binary`: `sage` or an absolute path to Sage.
- `estimator.lattice_estimator_path`: absolute path to `malb/lattice-estimator`
  if Sage cannot already import `estimator`.
- `estimator.default_timeout_seconds`: request-level timeout for optional
  estimator validation.
- `model.base_url`, `model.model`, `model.api_key_env`: bring-your-own model
  settings for the future agent layer.
- `scripts.decrypt_error`, `scripts.signature_smoothing`: future local script
  hooks for scheme-specific checks.

Equivalent environment variables:

```bash
SAGE_BINARY=/path/to/sage \
LATTICE_ESTIMATOR_PATH=/path/to/lattice-estimator \
AILATTICE_MODEL_BASE_URL=http://localhost:11434/v1 \
AILATTICE_MODEL=qwen2.5-coder:7b \
python3 -m app.server
```

The API exposes only non-secret public config at `/api/config/public`.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Scope

This prototype is not a production parameter certification tool. It does not yet
bind the RLWE instance to a concrete encryption/signature scheme, so it does not
compute decryption failure, signing rejection/failure, smoothing-parameter
conditions, or complete reduction-loss accounting.

The `matzov` red-cost option means the classical ADPS16 Matzov-style
dual-hybrid estimate. The `adps16` option reports the ADPS16 CoreSVP/uSVP
estimate. With Sage validation enabled, AILattice calls `lattice-estimator` and
rounds bit counts downward to avoid overstating a lower bound.

## Planned Extension Points

- `agent`: convert user intent into constraints and explain tradeoffs.
- `estimators`: queue/cache long-running lattice-estimator jobs.
- `schemes/encryption`: decryption-error scripts for concrete PKE/KEM schemes.
- `schemes/signature`: hash-and-sign smoothing and rejection checks.
- `providers`: OpenAI-compatible, local Ollama/vLLM, or other user-owned model
  endpoints.

See [docs/references.md](docs/references.md) for scheme-design references used
to guide future extension work.
