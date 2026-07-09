# easyLattice Hugging Face Estimator Worker

This directory is a Docker Space template for live Sage/lattice-estimator runs.
It is intentionally separate from the public GitHub Pages frontend.

## Deployment

Create a Hugging Face Space with SDK `Docker`, then copy the full easyLattice
repository into the Space repository. The Space repository root must contain a
file named `Dockerfile`, so copy this template there:

```bash
cp deploy/huggingface-estimator/Dockerfile Dockerfile
```

Do not upload only `deploy/huggingface-estimator`; the worker imports
`app/estimator_runner.py` from the project root.

The container:

- starts from a SageMath image;
- clones `malb/lattice-estimator` into `/opt/lattice-estimator`;
- exposes a small JSON API on port `7860`;
- defaults to a 240 second timeout and clamps requests to 300 seconds.

Useful environment variables:

```text
EASYLATTICE_ESTIMATOR_TIMEOUT_SECONDS=240
EASYLATTICE_ESTIMATOR_MAX_TIMEOUT_SECONDS=300
EASYLATTICE_ESTIMATOR_WORKERS=1
EASYLATTICE_ALLOWED_ORIGINS=https://your-github-pages-domain.example
```

Use `EASYLATTICE_ALLOWED_ORIGINS=*` only for early testing.

## API

Health:

```bash
curl https://YOUR-SPACE.hf.space/health
```

Submit an async job:

```bash
curl -X POST https://YOUR-SPACE.hf.space/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "timeout_seconds": 240,
    "payload": {
      "n": 512,
      "q": 257,
      "distribution": {
        "estimator": {"type": "centered_binomial", "eta": 1}
      },
      "per_attack_timeout": 30
    }
  }'
```

Poll:

```bash
curl https://YOUR-SPACE.hf.space/jobs/JOB_ID
```

Synchronous debugging endpoint:

```bash
curl -X POST https://YOUR-SPACE.hf.space/estimate \
  -H 'Content-Type: application/json' \
  -d '{"timeout_seconds": 240, "payload": {...}}'
```

The public frontend should use `/jobs` plus polling. Avoid long browser
requests to `/estimate`; a proxy or browser may time out before Sage does.

## Safety Defaults

The worker does not accept arbitrary Python or shell commands. It only forwards
validated estimator payloads to `app/estimator_runner.py`.

Current limits:

- `n <= 16384`;
- `q` at most 64 bits;
- `problem` is `lwe` or `ntru`;
- NTRU `ntru_type` is `circulant` or `matrix`;
- timeout is clamped by `EASYLATTICE_ESTIMATOR_MAX_TIMEOUT_SECONDS`;
- jobs are in-memory and expire after one hour by default.
