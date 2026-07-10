# easyLattice

**English** | [中文](README.zh.md)

Local-first, open-source prototype for lattice-cryptography parameter selection.

The public GitHub Pages site is a static interface preview. It has no shared
backend and does not call an LLM, Sage, or `lattice-estimator`. For live
calculation, clone the repository and start the local server; Sage, estimator,
and optional model configuration remain on your own machine.

## Overview

easyLattice is layered so the default path does not require an LLM token:

1. deterministic core: fixed search policy and local security screening;
2. estimator adapter: optional user-provided Sage/lattice-estimator validation;
3. agent layer: deterministic by default, with an optional LLM intent parser;
4. provider layer: user-owned OpenAI-compatible endpoint and authentication.

The RLWE selector supports:

- power-of-two cyclotomic rings `Z_q[x] / (x^n + 1)`;
- ternary cyclotomic rings `Z_q[x] / (x^n - x^(n/2) + 1)` for even `n` whose
  prime factors are only `2` and `3`;
- NTT-friendly prime moduli with `n | q - 1`; full splitting `2n | q - 1` is
  preferred, while one unresolved NTT layer is treated as nearly as good;
- centered-binomial and iid sparse-ternary secret distributions;
- independently searched centered-binomial or iid sparse-ternary error
  distributions for LWE/RLWE/MLWE, using a fixed-weight estimator approximation
  for sparse ternary;
- compression-noise errors for LWR/RLWR/MLWR, generated from the selected
  `q -> p` compression modulus;
- fast local screening plus optional Sage/lattice-estimator validation.

The same agent API also includes an initial NTRU selector:

- power-of-two cyclotomic NTRU over `Z_q[x] / (x^n + 1)`, matching ring
  families used by NEV/BAT/DAWN-style variants;
- the relaxed power-of-two NTT default `n/2 | q - 1`;
- two-stage distribution selection: a discrete-Gaussian proxy first calibrates
  the minimum standard deviation, then a closest fast-sampling distribution is
  chosen. Fast distributions can be one block or short sums of sparse ternary,
  symmetric uniform, and centered-binomial blocks. Summed distributions are
  estimator moment approximations capped by the Gaussian calibration;
- HPS-like and HRSS-like comparison candidates;
- optional local NTRU validation with MATZOV and ADPS16 classical and quantum
  cost models. A quantum NTRU target therefore requires Sage estimation rather
  than the classical-only fast reference screen.

## Search Model

Requested security is a lower bound. The selector first chooses the
polynomial/ring family, then degree `n`, then the smallest modulus satisfying
the requested NTT scale, and then the secret and error distributions. Within a
fixed modulus, it avoids unnecessary security margin.

JSON output separates `secret` and `error` fields. For LWE/RLWE/MLWE, the
prototype searches `Xs` and `Xe` independently. For LWR/RLWR/MLWR, the secret
selector controls `Xs`, while the error control is a compression modulus `p`.
The error distribution is the centered compression-noise law induced by
compressing `vi in {0, ..., q-1}` from `q` to `p` and lifting back to `q`.

The screening heuristic is monotone in the expected directions: smaller `q`,
larger dimension, and larger error standard deviation usually increase
LWE/RLWE hardness. Correctness and scheme encoding can impose the opposite
constraints, so those belong in scheme-specific modules.

For sparse ternary candidates, easyLattice includes
`Pr[+1] = Pr[-1] = (2^l0 - 1) / 2^(2*l0 + l1)`, with the remaining probability
on `0`. These are inexpensive to sample with bit operations. Because
`lattice-estimator` models sparse ternary vectors by fixed Hamming weight,
easyLattice passes expected `+1` and `-1` counts as a fixed-weight
approximation and reports it in the JSON output.

easyLattice is a local tool, not a hosted parameter-certification service.
Users provide their own estimator installation, optional model endpoint/API
key, and scheme-specific scripts for error correction, rejection sampling, or
smoothing parameters. The LLM layer is disabled unless `llm.enabled=true` is
set locally; when enabled, it only turns free-form intent into deterministic
search constraints.

## Public Preview and Local Running

[GitHub Pages](https://icarid-liu.github.io/easyLattice/) is a static preview
of the interface. It is intended for inspection only and does not run a shared
backend.

For live parameter search and DFR calculation, clone the repository and start
the local service:

```bash
git clone https://github.com/Icarid-Liu/easyLattice.git
cd easyLattice
./scripts/setup-local.sh --start
```

Open `http://127.0.0.1:8000`. The setup script creates `config.local.json`,
detects local Sage/lattice-estimator paths where possible, and keeps optional
LLM settings local. Manual startup is also supported with `python3 -m app.server`.

The following representative prototype settings are examples only; use the
local server for live output. They are not parameter certifications.

Controls used for the table:

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
| LWE / LWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | example |
| LWE / RLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | example |
| LWE / LWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | example |
| LWE / RLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | example |
| LWE / MLWE | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | example |
| LWE / MLWR | 512 | 257 | `n/2 \| q - 1` | `ST(l0=4,l1=2)` | `CompressNoise(p=3)` | 3 | 528.3 | example |
| SIS / SIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | taxonomy placeholder |
| SIS / MSIS | 512 | 257 | `n/2 \| q - 1` | `ST(l0=1,l1=0)` | `ST(l0=3,l1=2)` | - | 129.6 | taxonomy placeholder |

`SIS / SIS` and `SIS / MSIS` are visible in the current UI taxonomy, but a
real SIS/MSIS selector is not implemented yet. Their rows reuse the current
LWE/RLWE fast-screen scaffold and are not SIS hardness estimates.

## Decryption Failure Rate

The local UI provides a standalone finite-distribution DFR calculator with the
following pre-error-correction coefficient models:

- NTRU: `p0*(g*s)_n + p1*(f*e)_n + p2*(f*m)_n + p3*e`;
- LWE: `((e1 + ec1)*s)_m + (e*r)_m + e2 + ec2`.

The success boundary is `|E| <= Delta`. DFR is reported as `log2(DFR)` for a
single coefficient and for the vector union bound. Explicit raw-probability
fields remain in the API and copied JSON for external ECC calculations. The
default working precision is 512 bits, and discrete Gaussians use a
configurable 128-bit tail bound.

Inputs support common `lattice-estimator` distribution families, LWR floor
compression, Kyber nearest-integer compression, and a custom finite PMF JSON
object. Estimator `NoiseDistribution` instances expose moments rather than a
unique sampling law, so DFR calculation requires a custom PMF for them.
Fixed-weight sparse ternary inputs use their coefficient marginal and report
the resulting correlation approximation.

The calculator deliberately does not model error correction. LAC, DAWN, and
other coded schemes should pass its pre-correction outputs to a scheme-specific
correction-probability script.

## Local Checkout

For a fresh local checkout, the simplest start is:

```bash
./scripts/setup-local.sh --start
```

The script creates `config.local.json`, keeps LLM disabled, detects optional
Sage/lattice-estimator paths, runs a small smoke test, and starts the service
at `http://127.0.0.1:8000`.

To create local configuration without starting the server:

```bash
./scripts/setup-local.sh
```

To clone `malb/lattice-estimator` into `.external/lattice-estimator` when no
local estimator path is detected:

```bash
./scripts/setup-local.sh --with-estimator
```

Sage remains optional for fast-screen mode and is required only when
`useEstimator=true`. Manual startup is also supported:

```bash
python3 -m app.server
```

Open `http://127.0.0.1:8000`, or use another port when needed:

```bash
PORT=8010 python3 -m app.server
```

## Local Configuration

The setup script is preferred. For manual configuration, copy the example:

```bash
cp config.local.example.json config.local.json
```

`config.local.json` is ignored by git. Relevant settings include:

- `estimator.sage_binary`: `sage` or an absolute Sage executable path;
- `estimator.lattice_estimator_path`: absolute path to `malb/lattice-estimator`
  when Sage cannot already import `estimator`;
- `estimator.default_timeout_seconds`: request-level estimator timeout;
- `estimator.remote_url`: optional Hugging Face estimator worker URL;
- `estimator.remote_timeout_seconds`: remote-worker timeout, intended for
  180-300 second runs;
- `estimator.remote_poll_interval_seconds`: remote-job polling interval;
- `llm.enabled`: disabled by default; set to `true` only for LLM intent parsing;
- `llm.base_url`, `llm.model`, `llm.api_key_env`, `llm.auth_header`, and
  `llm.auth_prefix`: user-owned OpenAI-compatible model settings;
- `scripts.decrypt_error` and `scripts.signature_smoothing`: future local
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

For local endpoints without authentication, set `"auth_header": ""` in
`config.local.json`. The API exposes only non-secret configuration at
`/api/config/public`.

## API

The main recommendation endpoint is:

```text
POST /api/agent/recommend
```

With `useLLM=false` or omitted, it runs only the deterministic core. With
`useLLM=true`, it requires local LLM configuration and an `intent` string. The
legacy-compatible `/api/rlwe/recommend` route remains available and uses the
same agent layer.

Long estimator runs use asynchronous jobs:

```text
POST /api/agent/jobs
GET /api/agent/jobs/{job_id}
```

The browser uses those endpoints when `useEstimator=true`, so 3-5 minute Sage
or lattice-estimator runs do not depend on one long HTTP request.

The synchronous decryption-failure endpoint is:

```text
POST /api/decryption-failure/calculate
```

It accepts `type: "ntru" | "lwe"`, dimensions, coefficients, distribution
objects, and optional `precisionBits` / `tailBits`. It returns pre-correction
`log2(DFR)`, explicit raw-probability fields for ECC scripts, support summaries,
tail bounds, and approximation warnings.

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

GitHub Pages has no shared live backend. To self-host dynamic estimation,
[`deploy/huggingface-live`](deploy/huggingface-live) provides a Docker template
for the deterministic selector and optional Sage/lattice-estimator validation
behind the same API as the local server. Hugging Face may require a paid PRO
account for Docker Spaces.

For a smaller estimator-only worker,
[`deploy/huggingface-estimator`](deploy/huggingface-estimator) provides:

- `POST /jobs` for asynchronous estimator jobs;
- `GET /jobs/{job_id}` for polling;
- `POST /estimate` for synchronous debugging only;
- a default 240-second timeout, clamped to 300 seconds.

The worker accepts only validated estimator payloads and forwards them to
`app/estimator_runner.py`; it does not run arbitrary user code or any LLM.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Scope

This prototype is not a production parameter-certification tool. Its standalone
DFR calculator is not bound to a concrete encryption/signature encoding or an
error-correction code, and it does not compute rejection-sampling times,
smoothing-parameter conditions, or complete reduction-loss accounting.

The `matzov` cost option means the classical ADPS16 Matzov-style dual-hybrid
estimate. The `adps16` option reports the ADPS16 CoreSVP/uSVP estimate. With
Sage validation enabled, easyLattice calls `lattice-estimator` and rounds bit
counts downward to avoid overstating a lower bound.

## Planned Extension Points

- `agent`: turn user intent into constraints and explain tradeoffs; LLM help is
  opt-in;
- `estimators`: queue and cache long-running lattice-estimator jobs;
- `schemes/encryption`: decryption-error scripts for concrete PKE/KEM schemes;
- `schemes/signature`: hash-and-sign smoothing and rejection checks;
- `providers`: OpenAI-compatible, local Ollama/vLLM, or other user-owned model
  endpoints. Providers must never use maintainer-owned tokens.

See [docs/references.md](docs/references.md) for scheme-design references and
[docs/architecture.md](docs/architecture.md) for the deterministic-core and
optional-LLM layering.
