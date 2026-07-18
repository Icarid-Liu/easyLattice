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
- checks out `malb/lattice-estimator` at pinned revision
  `3e48ef421ec256afddb3e7d2249a77eab6e9ba12` into
  `/opt/lattice-estimator`;
- checks out `identitymapping/enhanced_lattice-estimator` at pinned revision
  `876b66173f4354a96ddafc0ce3a79767ec43c6d4` into
  `/opt/enhanced-lattice-estimator`;
- exposes a small JSON API on port `7860`;
- defaults to a 240 second timeout and clamps requests to 300 seconds.

Useful environment variables:

```text
EASYLATTICE_ESTIMATOR_TIMEOUT_SECONDS=240
EASYLATTICE_ESTIMATOR_MAX_TIMEOUT_SECONDS=300
EASYLATTICE_ESTIMATOR_WORKERS=1
EASYLATTICE_ALLOWED_ORIGINS=https://your-github-pages-domain.example
SAGE_BINARY=sage
LATTICE_ESTIMATOR_PATH=/opt/lattice-estimator
ENHANCED_LATTICE_ESTIMATOR_PATH=/opt/enhanced-lattice-estimator
```

Use `EASYLATTICE_ALLOWED_ORIGINS=*` only for early testing. CORS is not access
control: it only limits which browser origins may read responses, and it does
not authenticate callers or stop scripts and other non-browser clients from
calling the API directly. The standalone `/jobs`, `/jobs/{job_id}`, and
`/estimate` endpoints are unauthenticated and have no per-client rate limit;
the worker/job caps below are only global resource bounds. Do not expose this
compute-intensive worker as an unrestricted public service. Production
deployments require either a private Space or an authenticated, rate-limited
gateway in front of the worker. Treat `EASYLATTICE_ALLOWED_ORIGINS` only as an
additional browser-origin restriction.

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
      "estimator_profile": "enhanced",
      "hard_problem_variant": "rlwe",
      "ring_degree": 512,
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
  -d '{
    "timeout_seconds": 240,
    "payload": {
      "problem": "lwe",
      "n": 512,
      "q": 257,
      "estimator_profile": "standard",
      "hard_problem_variant": "lwe",
      "ring_degree": 512,
      "distribution": {
        "estimator": {"type": "centered_binomial", "eta": 1}
      },
      "per_attack_timeout": 30
    }
  }'
```

The public frontend should use `/jobs` plus polling. Avoid long browser
requests to `/estimate`; a proxy or browser may time out before Sage does.

## Estimator Profiles

Each payload may set `estimator_profile` to `standard` or `enhanced`.
LWE/LWR and NTRU jobs use `standard`; RLWE/MLWE/RLWR/MLWR jobs use `enhanced`.
The full application adds this field automatically. Direct worker callers must
send the correct profile themselves. For example, a plain LWE payload uses:

```json
{
  "estimator_profile": "standard",
  "hard_problem_variant": "lwe"
}
```

The two estimator repositories both provide a top-level package named
`estimator`. The worker never imports both into one process. It selects
`LATTICE_ESTIMATOR_PATH` or `ENHANCED_LATTICE_ESTIMATOR_PATH`, starts a separate
Sage subprocess with only that source root on `PYTHONPATH`, disables the user
site, and verifies the imported package origin before running the estimate.

For enhanced structured LWE payloads, all of `usvp`, `dual_hybrid`, and
`bdd_hybrid` run in the enhanced profile. At the pinned enhanced revision,
explicit `deg_ring`/`structure_leverage` correction exists only for
`bdd_hybrid`. Enhanced `dual_hybrid` has no explicit ring-structure correction:
its finite result is reported with `structure_correction.applied=false` and
`available=false`, is kept for inspection, and makes structured validation
partial. It cannot certify the target alone. MATZOV and ADPS16 are evaluated in
classical and quantum modes. NTRU payloads use the standard profile.

## Safety Defaults

The worker does not accept arbitrary Python or shell commands. It only forwards
validated estimator payloads to `app/estimator_runner.py`.

Current limits:

- `n <= 16384`;
- `q` at most 64 bits;
- `problem` is `lwe` or `ntru`;
- `estimator_profile` is `standard` or `enhanced`;
- NTRU `ntru_type` is `circulant` or `matrix`;
- timeout is clamped by `EASYLATTICE_ESTIMATOR_MAX_TIMEOUT_SECONDS`;
- jobs are in-memory and expire after one hour by default.
