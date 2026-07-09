# easyLattice Hugging Face Live API

This directory is a Docker Space template for the public live backend. Unlike
`deploy/huggingface-estimator`, this Space exposes the full easyLattice API:

- deterministic parameter selection at `POST /api/agent/recommend`;
- optional Sage/lattice-estimator refinement when `useEstimator=true`;
- public configuration at `GET /api/config/public`;
- the same browser UI served by the Space itself.

The Docker Space uses the Hugging Face Docker SDK on port `7860`, installs
Sage/lattice-estimator, and sets a 240 second default estimator timeout with a
300 second request cap.

## Deploy

You need a Hugging Face write token. From the project root:

```bash
python3 -m pip install huggingface_hub
HF_TOKEN=hf_xxx python3 deploy/huggingface-live/deploy_space.py \
  --repo-id YOUR_HF_NAME/easyLattice-live \
  --public
```

The script creates or updates the Space and uploads a clean deploy context. It
does not upload `.git`, local config files, caches, or test output.

After Hugging Face finishes building, test:

```bash
curl https://YOUR_HF_NAME-easyLattice-live.hf.space/api/health
```

For a quick estimator run:

```bash
curl -X POST https://YOUR_HF_NAME-easyLattice-live.hf.space/api/agent/recommend \
  -H 'Content-Type: application/json' \
  -d '{
    "hardProblemCategory": "LWE",
    "hardProblemVariant": "RLWR",
    "targetSecurity": 128,
    "securityModel": "classical",
    "redCostModel": "matzov",
    "nttScalePower": 1,
    "maxQBits": 24,
    "distribution": "auto",
    "useEstimator": true,
    "estimatorTimeout": 240
  }'
```

## Environment

The Dockerfile sets:

```text
HOST=0.0.0.0
PORT=7860
SAGE_BINARY=sage
LATTICE_ESTIMATOR_PATH=/opt/lattice-estimator
EASYLATTICE_ESTIMATOR_TIMEOUT=240
EASYLATTICE_ESTIMATOR_PER_ATTACK_TIMEOUT=60
EASYLATTICE_ALLOWED_ORIGINS=*
```

For production, replace `EASYLATTICE_ALLOWED_ORIGINS=*` in the Space settings
with your GitHub Pages origin, for example:

```text
https://icarid-liu.github.io
```
