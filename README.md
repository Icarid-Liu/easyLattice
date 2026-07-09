# easyLattice

Local-first, open-source prototype for lattice-crypto parameter selection.

The public GitHub Pages site is a static example. It does not run a backend,
call an LLM, call Sage, or call `lattice-estimator`. Dynamic estimation is
available only when you run the local service or deploy your own backend.

easyLattice is layered so the default path does not require any LLM token:

1. deterministic core: fixed RLWE search policy and local security screening;
2. estimator adapter: optional user-provided Sage/lattice-estimator validation;
3. agent layer: deterministic by default, with an optional LLM intent parser;
4. provider layer: user-owned OpenAI-compatible endpoint and authentication.

This first version focuses on a basic RLWE instance selector:

- power-of-two cyclotomic ring `Z_q[x] / (x^n + 1)`;
- ternary cyclotomic ring `Z_q[x] / (x^n - x^(n/2) + 1)` for even `n` whose
  prime factors are only `2` and `3`;
- NTT-friendly prime modulus with `n | q - 1`; full splitting
  `2n | q - 1` is preferred, but leaving one NTT layer unresolved is treated as
  nearly as good;
- centered binomial and iid sparse ternary secret distributions;
- paired centered binomial or iid sparse ternary error distributions for
  LWE/RLWE/MLWE, with fixed-weight estimator approximation for sparse ternary;
- uniform rounding-error distributions for LWR/RLWR/MLWR, reported with the
  derived LWR modulus `p`;
- fast local screening plus optional user-provided Sage/lattice-estimator validation;
- a small web UI for interactive parameter search.

There is also an initial NTRU selector behind the same agent API. It currently
supports:

- power-of-two cyclotomic NTRU over `Z_q[x] / (x^n + 1)`, matching the ring
  family used by designs such as NEV/BAT/DAWN-style NTRU variants;
- the same relaxed NTT default used by the RLWE prototype for power-of-two
  rings, namely `n/2 | q - 1`;
- two-stage distribution selection: first calibrate the minimum standard
  deviation with a discrete-Gaussian proxy, then choose the closest
  fast-sampling distribution whose standard deviation is above that lower
  bound. Fast distributions may be single blocks or short sums of sparse
  ternary, symmetric uniform, and centered-binomial blocks; summed
  distributions are estimator moment approximations and are capped by the
  Gaussian proxy calibration to avoid overstating security;
- HPS-like and HRSS-like comparison candidates;
- local `lattice-estimator` NTRU rough validation when `useEstimator=true`.

The selector treats the user's requested security as a lower bound. It first
chooses the polynomial/ring family, then degree `n`, then the smallest modulus
satisfying the chosen NTT scale, and only then chooses the secret/error
distribution for that modulus. Within a fixed modulus it still avoids
unnecessary security margin.

The JSON output separates `secret` and `error` distribution fields. For
LWE/RLWE/MLWE the current prototype searches paired `Xs = Xe` distributions. For
LWR/RLWR/MLWR, the distribution selector controls only the secret; the
rounding-error distribution is always uniform, and each recommendation reports
the corresponding LWR `p` as the size of the uniform error support.

The basic monotonicity heuristic remembered by the selector is: smaller `q`
usually increases LWE/RLWE hardness, larger dimension increases hardness, and
larger error standard deviation increases hardness. Correctness and scheme
encoding may push in the opposite direction, so those checks belong in
scheme-specific modules.

For sparse ternary candidates, easyLattice includes distributions with
`Pr[+1] = Pr[-1] = (2^l0 - 1) / 2^(2*l0 + l1)` and all remaining probability on
`0`. These are cheap to sample with bit operations. Since `lattice-estimator`
models sparse ternary vectors by fixed Hamming weight, easyLattice passes the
expected `+1` and `-1` counts as a fixed-weight approximation and reports that
approximation in the JSON output.

easyLattice is designed as a local tool, not a hosted service. Users bring their
own estimator installation, optional model endpoint/API key, and later their
own scheme-specific scripts such as decryption-error, rejection-sampling, or
smoothing-parameter calculators.

No API key is required for the default RLWE workflow. The LLM layer is disabled
unless `llm.enabled=true` is set locally. When enabled, the model only converts
free-form user intent into deterministic search constraints; final parameters
still come from the fixed local search logic and optional estimator validation.

## Public Static Example

The hosted GitHub Pages version is intentionally static. It demonstrates the UI
and fixed example outputs for this prototype, but all values should be treated
as examples rather than live parameter certification.

The table below fixes the controls to:

- target security: `128`;
- security metric: `Classical`;
- reduction cost model: `MATZOV`;
- distribution: `Auto`;
- ring family: `x^n + 1`;
- NTT scale: `n/2 | q - 1`;
- estimator validation: off.

| Public UI option | n | q | NTT condition | Secret distribution | Error distribution | LWR p | Classical bits | Status |
| --- | ---: | ---: | --- | --- | --- | ---: | ---: | --- |
| NTRU / matrix | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2) + ST(l0=4,l1=0) + ST(l0=4,l1=0)` | same | - | 128.0 | example |
| NTRU / ring | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2) + ST(l0=4,l1=0) + ST(l0=4,l1=0)` | same | - | 128.0 | example |
| LWE / LWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=2,l1=0)` | `ST(l0=2,l1=0)` | - | 129.7 | example |
| LWE / RLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=2,l1=0)` | `ST(l0=2,l1=0)` | - | 129.7 | example |
| LWE / LWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `Uniform(-1,1)` | 3 | 141.1 | example |
| LWE / RLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `Uniform(-1,1)` | 3 | 141.1 | example |
| LWE / MLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=2,l1=0)` | `ST(l0=2,l1=0)` | - | 129.7 | example |
| LWE / MLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `Uniform(-1,1)` | 3 | 141.1 | example |
| SIS / SIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=2,l1=0)` | `ST(l0=2,l1=0)` | - | 129.7 | taxonomy placeholder |
| SIS / MSIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=2,l1=0)` | `ST(l0=2,l1=0)` | - | 129.7 | taxonomy placeholder |

`SIS / SIS` and `SIS / MSIS` are shown in the current UI taxonomy, but a real
SIS/MSIS selector is not implemented yet. Their rows reuse the current
LWE/RLWE fast-screen scaffold and should not be read as SIS hardness estimates.

## Run

For a fresh local checkout, the simplest path is:

```bash
./scripts/setup-local.sh --start
```

The setup script creates `config.local.json`, keeps LLM disabled, detects
optional Sage/lattice-estimator paths when available, runs a small smoke test,
and then starts the web service at:

```text
http://127.0.0.1:8000
```

If you only want to generate local config without starting the server:

```bash
./scripts/setup-local.sh
```

Optional estimator setup:

```bash
./scripts/setup-local.sh --with-estimator
```

This clones `malb/lattice-estimator` into `.external/lattice-estimator` if no
local estimator path is detected. Sage is still optional for fast-screen mode
and required only when `useEstimator=true`.

Manual start still works:

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

The setup script above is preferred. For manual configuration, copy the example
file and edit local paths:

```bash
cp config.local.example.json config.local.json
```

`config.local.json` is ignored by git. It can contain:

- `estimator.sage_binary`: `sage` or an absolute path to Sage.
- `estimator.lattice_estimator_path`: absolute path to `malb/lattice-estimator`
  if Sage cannot already import `estimator`.
- `estimator.default_timeout_seconds`: request-level timeout for optional
  estimator validation.
- `estimator.remote_url`: optional Hugging Face estimator worker URL. When set,
  `useEstimator=true` calls this remote worker instead of local Sage.
- `estimator.remote_timeout_seconds`: remote worker timeout, intended for
  180-300 second live estimator runs.
- `estimator.remote_poll_interval_seconds`: polling interval for remote jobs.
- `llm.enabled`: disabled by default. Set to `true` only when you want LLM
  intent parsing.
- `llm.base_url`, `llm.model`, `llm.api_key_env`, `llm.auth_header`,
  `llm.auth_prefix`: bring-your-own OpenAI-compatible model settings.
- `scripts.decrypt_error`, `scripts.signature_smoothing`: future local script
  hooks for scheme-specific checks.

Equivalent environment variables:

```bash
SAGE_BINARY=/path/to/sage \
LATTICE_ESTIMATOR_PATH=/path/to/lattice-estimator \
python3 -m app.server
```

Remote estimator worker:

```bash
EASYLATTICE_ESTIMATOR_REMOTE_URL=https://your-estimator-space.hf.space \
EASYLATTICE_ESTIMATOR_REMOTE_TIMEOUT=240 \
python3 -m app.server
```

Optional LLM enhancement:

```bash
export EASYLATTICE_LLM_ENABLED=true
export EASYLATTICE_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
export EASYLATTICE_LLM_MODEL=your-model
export EASYLATTICE_LLM_API_KEY=your-token
python3 -m app.server
```

For local endpoints that do not require authentication, set
`"auth_header": ""` in `config.local.json`.

The API exposes only non-secret public config at `/api/config/public`.

The main recommendation endpoint is:

```text
POST /api/agent/recommend
```

With `useLLM=false` or omitted, it runs only the deterministic core. With
`useLLM=true`, it requires local LLM configuration and an `intent` string.
The legacy-compatible `/api/rlwe/recommend` route is still available and uses
the same agent layer.

For long estimator runs, the live API also exposes async recommendation jobs:

```text
POST /api/agent/jobs
GET /api/agent/jobs/{job_id}
```

The browser UI uses these job endpoints when `useEstimator=true`, so 3-5 minute
Sage/lattice-estimator runs do not depend on a single long HTTP request.

Use `"problem": "ntru"` to call the NTRU selector:

```json
{
  "problem": "ntru",
  "targetSecurity": 128,
  "ringFamily": "power2",
  "useEstimator": true
}
```

## Optional Live Backend

The public GitHub Pages site does not use a live backend. If you want to
self-host dynamic estimation later, the Docker template in
[`deploy/huggingface-live`](deploy/huggingface-live) runs the deterministic
selector and optional Sage/lattice-estimator validation behind the same API as
the local server. Hugging Face may require a paid PRO account for Docker Spaces.

For a smaller estimator-only worker, the template in
[`deploy/huggingface-estimator`](deploy/huggingface-estimator) exposes:

- `POST /jobs` for async estimator jobs;
- `GET /jobs/{job_id}` for polling;
- `POST /estimate` for synchronous debugging only;
- a default 240 second timeout, clamped to a 300 second maximum.

The estimator-only worker accepts only validated estimator payloads and forwards
them to `app/estimator_runner.py`; it does not run arbitrary user code or any
LLM.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Scope

This prototype is not a production parameter certification tool. It does not yet
bind the RLWE instance to a concrete encryption/signature scheme, so it does not
compute decryption failure, rejection sampling times, smoothing-parameter
conditions, or complete reduction-loss accounting.

The `matzov` red-cost option means the classical ADPS16 Matzov-style
dual-hybrid estimate. The `adps16` option reports the ADPS16 CoreSVP/uSVP
estimate. With Sage validation enabled, easyLattice calls `lattice-estimator` and
rounds bit counts downward to avoid overstating a lower bound.

## Planned Extension Points

- `agent`: convert user intent into constraints and explain tradeoffs. The
  default implementation is deterministic; LLM assistance is opt-in.
- `estimators`: queue/cache long-running lattice-estimator jobs.
- `schemes/encryption`: decryption-error scripts for concrete PKE/KEM schemes.
- `schemes/signature`: hash-and-sign smoothing and rejection checks.
- `providers`: OpenAI-compatible, local Ollama/vLLM, or other user-owned model
  endpoints. Providers must never use maintainer-owned tokens.

See [docs/references.md](docs/references.md) for scheme-design references used
to guide future extension work. See [docs/architecture.md](docs/architecture.md)
for the deterministic-core and optional-LLM layering.
