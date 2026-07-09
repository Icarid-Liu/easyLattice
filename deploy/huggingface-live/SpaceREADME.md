---
title: easyLattice Live API
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
license: mit
---

# easyLattice Live API

This Space runs the easyLattice deterministic parameter search and optional
Sage/lattice-estimator refinement.

Useful endpoints:

- `GET /api/health`
- `GET /api/config/public`
- `POST /api/agent/recommend`

The default estimator timeout is 240 seconds and request-level estimator
timeouts are clamped to 300 seconds.
