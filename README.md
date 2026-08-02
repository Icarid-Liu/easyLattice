# easyLattice

Local-first prototype for lattice-cryptography parameter search. The public
GitHub Pages site is only a static preview; live search runs on your machine.

## Start

```bash
git clone https://github.com/Icarid-Liu/easyLattice.git
cd easyLattice
./start.sh
```

`./start.sh` is the normal entry point. It creates or updates
`config.local.json`, runs a deterministic smoke test, starts the server at
`http://127.0.0.1:8000`, and opens the browser when supported. Sage and
`lattice-estimator` are optional for the fast screen; configure their paths in
the browser when live validation is needed.

Useful options:

```bash
./start.sh --with-estimator       # clone missing Standard/Enhanced estimators
./start.sh --no-open              # do not open a browser
./start.sh --host 127.0.0.1 --port 8003
```

`--with-estimator` is not required for ordinary startup. It only clones the
two estimator repositories into `.external/`. Local jobs have no automatic
wall-clock deadline; a short per-attack watchdog prevents one Sage reduction
from blocking the whole search, and remote workers retain their configured
whole-job timeout.

## Search model

The deterministic search enumerates candidates in the order

\[
n\;\longrightarrow\;q\;\longrightarrow\;(X_s,X_e),
\]

where `q` is the smallest prime satisfying the requested NTT condition. With
estimator validation enabled, each candidate retains four results:

- MATZOV classical and quantum;
- ADPS16 classical and quantum.

The first measured candidate satisfying the selected security model and cost
model is returned. Exhaustion is reported as `no_feasible_candidate`, with a
best unmet reference candidate rather than a false recommendation.

Supported families include RLWE/MLWE/LWE/LWR variants and power-of-two, HPS,
HRSS, and NTRU-Prime-style NTRU candidates. RLWE-family variants use the
Enhanced estimator profile; LWE/LWR/NTRU use Standard.

## Distributions

Secret and Error are independent modules. Both default to `pure` and may be
switched independently to `combination`. Combination mode enumerates additive
CBD and sparse-ternary components up to `maxDistributionComponents` (default
`3`, allowed range `1..6`). For a composite distribution,

\[
\operatorname{Var}(X_1+\cdots+X_k)=\sum_i\operatorname{Var}(X_i),
\]

and the estimator receives a conservative moment approximation with a warning.
For LWR/RLWR/MLWR, Error is compression noise induced by `q -> p`; its
distribution selector is disabled.

Sparse ternary uses

\[
\Pr[X=+1]=\Pr[X=-1]=
\frac{2^{\ell_0}-1}{2^{2\ell_0+\ell_1}}.
\]

Results expose `P(+1)`, `P(-1)`, `P(0)`, and support `[-1, 0, 1]`. For a fixed
`(n, q)`, the search builds two independent distribution tables. It first
locates the smallest combined standard deviation that reaches the target
(binary search when the measured target predicate is monotone, with an exact
fallback otherwise). It then chooses the minimum Secret+Error sampling-bit
budget among rows at or above that width; equal sampling costs prefer the
smaller width. The JSON exposes both `min_sampling_bits` and `min_stddev`
recommendations.

The fast screen is not a scheme proof: correctness, rejection sampling,
smoothing, and error correction remain scheme-specific.

## Cost-model labels

- **MATZOV**（考虑多项式及亚指数部分复杂度，更激进）
- **ADPS16**（只考虑指数部分，更保守）

The chosen model and classical/quantum mode determine the target check; all
four comparison fields remain visible in the JSON response.

## Configuration

The browser persists only local Sage, Standard estimator, and Enhanced
estimator paths in `config.local.json`. LLM use is disabled by default and is
never needed by deterministic search. A remote estimator can be configured in
the same file; local and remote execution modes are reported separately.

## Tests

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/app-model.test.cjs
```

Optional live Sage fixtures:

```bash
EASYLATTICE_RUN_SAGE_TESTS=1 \
  python3 -m unittest discover -s tests -p 'test_live_estimator_search.py' -v
```

See [README.zh.md](README.zh.md) for the concise Chinese version.
