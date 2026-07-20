# Security, Ring, and DFR Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route each lattice problem to the correct estimator, report validation and target states honestly, add Streamlined NTRU Prime, model NTRU DFR in three polynomial rings, and keep the bilingual browser UI synchronized with effective inputs.

**Architecture:** Standard and enhanced estimators run in separate Sage subprocesses selected by a shared adapter. Search responses share one validation/selection contract, while NTRU DFR delegates coefficient-reduction structure to a standalone polynomial-ring module. A small UMD JavaScript model owns ring options, result presentation, and request-revision checks so browser behavior can be tested with Node without a DOM framework.

**Tech Stack:** Python 3.10+, SageMath, `malb/lattice-estimator`, `identitymapping/enhanced_lattice-estimator`, `unittest`, high-precision `Decimal`, vanilla HTML/CSS/JavaScript, Node's built-in test runner.

## Global Constraints

- Do not modify either upstream estimator repository.
- Never import standard and enhanced packages named `estimator` in the same Python process.
- LWE and LWR use the standard estimator; RLWE, MLWE, RLWR, and MLWR use the enhanced estimator; NTRU uses the standard estimator.
- Enhanced `bdd_hybrid` is `LWE.primal_hybrid(..., mitm=False, babai=False, deg_ring=n, structure_leverage=True)` and adds `Grover=True` only in quantum mode.
- HPS, HRSS, and NTRU Prime security evaluation always uses `ntru_type="circulant"`.
- NTRU Prime DFR uses the approved coefficient-marginal approximation and emits an explicit correlation warning.
- The DFR success boundary remains `|E| <= Delta`; failures satisfy `|E| > Delta`.
- Vector DFR remains a union bound and does not assume independent output coefficients.
- Discrete-Gaussian truncation remains unchanged; its omitted tail stays separately reported and is treated as negligible for this prototype.
- Default DFR arithmetic precision remains 512 bits.
- Modulus width is `(q - 1).bit_length()`, equal to `ceil(log2(q))` for integer `q >= 2`.
- Dynamic result text must be localizable through stable codes; unknown estimator errors remain visible verbatim.
- Preserve the static GitHub Pages preview and the local-server execution model.

---

## File Map

### New Files

- `app/estimator_process.py`: estimator-profile routing, profile-specific paths, remote/local dispatch, and isolated Sage subprocess execution.
- `app/security_result.py`: shared modulus-width helper, validation summaries, selection status, and stable result codes.
- `app/polynomial_ring.py`: cyclic, negacyclic, and NTRU Prime raw-degree reduction and coefficient multiplicity profiles.
- `static/app-model.js`: DOM-free ring-option, status-presentation, row-filtering, and request-revision functions.
- `tests/test_estimator_runner.py`: attack-call and partial-result tests with fake estimator APIs.
- `tests/test_polynomial_ring.py`: brute-force ring-product and profile tests.
- `tests/js/app-model.test.cjs`: Node tests for frontend state and options.

### Modified Files

- `app/config.py`: standard/enhanced estimator configuration and public availability/revision data.
- `app/estimator_runner.py`: three-attack LWE execution and enhanced ring-aware arguments.
- `app/parameter_search.py`: profile routing, secret-aware validation scheduling, honest result states, and shared modulus widths.
- `app/ntru_search.py`: fixed circulant families, six SNTRUP presets, common response fields, and honest result states.
- `app/decryption_failure.py`: ring-aware NTRU coefficient PMFs, worst-coefficient DFR, and per-coefficient union bound.
- `app/server.py`: serve `app-model.js`.
- `scripts/setup-local.sh`: discover or clone both estimators and write both paths.
- `deploy/huggingface-estimator/space_app.py`: validate profiles and select the matching estimator root.
- `deploy/huggingface-estimator/Dockerfile`: install both estimators.
- `deploy/huggingface-live/Dockerfile`: install and configure both estimators.
- `static/index.html`: dynamic security ring selector hook, NTRU DFR ring selector, and frontend model script.
- `static/app.js`: result codes, field-aware rendering, stale-result handling, and request gating.
- `static/preview-data.js`: new response contract and three ring descriptions.
- `static/styles.css`: disabled/stale/status styling needed by the new states.
- `tests/test_agent_config.py`: profile configuration tests.
- `tests/test_parameter_search.py`: routing, scheduling, status, and bit-width tests.
- `tests/test_ntru_search.py`: SNTRUP, circulant, status, and optional-field tests.
- `tests/test_decryption_failure.py`: ring-aware DFR and union-bound tests.
- `tests/test_server.py`: static model asset test.
- `config.local.example.json`: enhanced estimator path.
- `README.md`, `README.zh.md`, `docs/architecture.md`: dual-estimator setup, result-state semantics, SNTRUP, and ring-aware DFR.
- `deploy/huggingface-estimator/README.md`, `deploy/huggingface-live/README.md`: dual-estimator deployment configuration.

---

### Task 1: Isolated Estimator Profiles and Local Setup

**Files:**
- Create: `app/estimator_process.py`
- Modify: `app/config.py:17-26, 67-117, 188-232`
- Modify: `scripts/setup-local.sh:12-27, 85-160`
- Modify: `config.local.example.json:2-11`
- Test: `tests/test_agent_config.py`

**Interfaces:**
- Produces: `estimator_profile_for(category: str, variant: str) -> str` returning `standard` or `enhanced`.
- Produces: `estimator_root(config: EstimatorConfig, profile: str) -> str | None`.
- Produces: `run_estimator(payload: dict, timeout: int, config: AppConfig, profile: str) -> dict`.
- Produces: `EstimatorConfig.enhanced_lattice_estimator_path: str | None`.
- Consumes: existing `estimate_remotely()` and `app/estimator_runner.py` stdin/stdout contract.

- [ ] **Step 1: Add failing configuration and routing tests**

Add these cases to `tests/test_agent_config.py`:

```python
from app.estimator_process import estimator_profile_for, estimator_root


def test_estimator_profiles_follow_problem_structure(self):
    for variant in ("lwe", "lwr"):
        self.assertEqual(estimator_profile_for("lwe", variant), "standard")
    for variant in ("rlwe", "mlwe", "rlwr", "mlwr"):
        self.assertEqual(estimator_profile_for("lwe", variant), "enhanced")
    self.assertEqual(estimator_profile_for("ntru", "ring"), "standard")
    self.assertEqual(estimator_profile_for("ntru", "matrix"), "standard")


def test_enhanced_estimator_path_is_loaded_and_publicly_reported(self):
    with TemporaryDirectory() as standard, TemporaryDirectory() as enhanced:
        for root, version in ((standard, "standard-test"), (enhanced, "enhanced-test")):
            package = Path(root) / "estimator"
            package.mkdir()
            (package / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        config = AppConfig(estimator=EstimatorConfig(
            lattice_estimator_path=standard,
            enhanced_lattice_estimator_path=enhanced,
        ))
        data = public_config(config)

    self.assertEqual(estimator_root(config.estimator, "standard"), standard)
    self.assertEqual(estimator_root(config.estimator, "enhanced"), enhanced)
    self.assertEqual(data["estimator"]["profiles"]["standard"]["revision"], "standard-test")
    self.assertEqual(data["estimator"]["profiles"]["enhanced"]["revision"], "enhanced-test")
    self.assertTrue(data["estimator"]["profiles"]["standard"]["available"])
    self.assertTrue(data["estimator"]["profiles"]["enhanced"]["available"])
```

- [ ] **Step 2: Run the focused tests and confirm the missing interfaces**

Run:

```bash
python3 -m unittest tests.test_agent_config -v
```

Expected: FAIL because `app.estimator_process` and `enhanced_lattice_estimator_path` do not exist.

- [ ] **Step 3: Implement estimator profile selection and isolated process dispatch**

Create `app/estimator_process.py` with this public shape:

```python
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import AppConfig, EstimatorConfig
from .remote_estimator import estimate_remotely


STANDARD_LWE_VARIANTS = {"lwe", "lwr"}
ENHANCED_LWE_VARIANTS = {"rlwe", "mlwe", "rlwr", "mlwr"}


def estimator_profile_for(category: str, variant: str) -> str:
    if category == "ntru":
        return "standard"
    if category == "lwe" and variant in STANDARD_LWE_VARIANTS:
        return "standard"
    if category == "lwe" and variant in ENHANCED_LWE_VARIANTS:
        return "enhanced"
    raise ValueError(f"No estimator profile for {category}/{variant}.")


def estimator_root(config: EstimatorConfig, profile: str) -> str | None:
    if profile == "standard":
        return config.lattice_estimator_path
    if profile == "enhanced":
        return config.enhanced_lattice_estimator_path
    raise ValueError("estimator profile must be standard or enhanced.")


def run_estimator(
    payload: dict[str, Any],
    timeout: int,
    config: AppConfig,
    profile: str,
) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["estimator_profile"] = profile
    if config.estimator.remote_url:
        return estimate_remotely(
            base_url=config.estimator.remote_url,
            payload=normalized,
            timeout_seconds=config.estimator.remote_timeout_seconds,
            poll_interval_seconds=config.estimator.remote_poll_interval_seconds,
        )
    return run_local_estimator(normalized, timeout, config.estimator, profile)


def run_local_estimator(
    payload: dict[str, Any],
    timeout: int,
    config: EstimatorConfig,
    profile: str,
) -> dict[str, Any]:
    sage = shutil.which(config.sage_binary) or (
        config.sage_binary if Path(config.sage_binary).exists() else None
    )
    if not sage:
        return {"ok": False, "code": "sage_not_found", "message": f"Sage binary '{config.sage_binary}' not found."}
    root = estimator_root(config, profile)
    if not root:
        return {"ok": False, "code": f"{profile}_estimator_not_configured", "message": f"{profile} estimator path is not configured."}

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    expanded = str(Path(root).expanduser())
    env["PYTHONPATH"] = expanded if not existing else f"{expanded}{os.pathsep}{existing}"
    runner = Path(__file__).with_name("estimator_runner.py")
    try:
        completed = subprocess.run(
            [sage, "-python", str(runner)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "estimator_timeout", "message": f"Estimator timed out after {timeout}s."}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        return {
            "ok": False,
            "code": "estimator_process_failed",
            "message": detail[0] if detail else f"Estimator exited with code {completed.returncode}.",
        }
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "code": "estimator_non_json", "message": "Estimator returned non-JSON output."}
```

Extend `EstimatorConfig`, `load_config()`, and `public_config()` so public data contains:

```python
"profiles": {
    "standard": {"available": True, "path": "/path", "revision": "abc1234"},
    "enhanced": {"available": True, "path": "/path", "revision": "def5678"},
}
```

Use `ENHANCED_LATTICE_ESTIMATOR_PATH` as the local environment override, while retaining `LATTICE_ESTIMATOR_PATH` for the standard estimator.

- [ ] **Step 4: Update setup discovery and generated configuration**

Make `scripts/setup-local.sh --with-estimator` discover or clone both:

```bash
git clone --depth=1 https://github.com/malb/lattice-estimator.git "$ROOT_DIR/.external/lattice-estimator"
git clone --depth=1 https://github.com/identitymapping/enhanced_lattice-estimator.git "$ROOT_DIR/.external/enhanced-lattice-estimator"
```

Write both keys in generated and example configuration:

```json
{
  "estimator": {
    "sage_binary": "sage",
    "lattice_estimator_path": "/absolute/path/to/lattice-estimator",
    "enhanced_lattice_estimator_path": "/absolute/path/to/enhanced-lattice-estimator",
    "default_timeout_seconds": 16,
    "per_attack_timeout_seconds": 12,
    "remote_url": null,
    "remote_timeout_seconds": 240,
    "remote_poll_interval_seconds": 2
  }
}
```

- [ ] **Step 5: Run configuration tests and shell syntax checks**

Run:

```bash
python3 -m unittest tests.test_agent_config -v
bash -n scripts/setup-local.sh
```

Expected: all configuration tests pass and the shell syntax check exits 0.

- [ ] **Step 6: Commit the profile boundary**

```bash
git add app/config.py app/estimator_process.py scripts/setup-local.sh config.local.example.json tests/test_agent_config.py
git commit -m "Add isolated estimator profiles"
```

---

### Task 2: Standard and Enhanced LWE Attack Execution

**Files:**
- Modify: `app/estimator_runner.py:84-184`
- Create: `tests/test_estimator_runner.py`
- Modify: `deploy/huggingface-estimator/space_app.py:157-295`
- Modify: `deploy/huggingface-estimator/Dockerfile:12-20`
- Modify: `deploy/huggingface-live/Dockerfile:12-20`

**Interfaces:**
- Consumes: payload keys `estimator_profile`, `hard_problem_variant`, and `ring_degree` from Task 1 callers.
- Produces: each runner response has `ok`, `complete`, `estimator_profile`, `estimator_commit`, `models`, and `modes`.
- Produces: each model/mode summary remains usable when at least one attack succeeds.

- [ ] **Step 1: Add failing attack-argument and partial-result tests**

Create `tests/test_estimator_runner.py` around a fake LWE API:

```python
import unittest
from unittest.mock import ANY

from app.estimator_runner import run_lwe_attack, summarize_attacks


class FakeLWE:
    calls = []

    @classmethod
    def primal_usvp(cls, params, **kwargs):
        cls.calls.append(("usvp", kwargs))
        return {"rop": 2**140}

    @classmethod
    def dual_hybrid(cls, params, **kwargs):
        cls.calls.append(("dual_hybrid", kwargs))
        return {"rop": 2**135}

    @classmethod
    def primal_hybrid(cls, params, **kwargs):
        cls.calls.append(("bdd_hybrid", kwargs))
        return {"rop": 2**130}


class EstimatorRunnerTests(unittest.TestCase):
    def setUp(self):
        FakeLWE.calls.clear()

    def test_enhanced_bdd_hybrid_receives_ring_arguments_and_quantum_grover(self):
        run_lwe_attack(FakeLWE, object(), "bdd_hybrid", object(), "classical", "enhanced", 512)
        run_lwe_attack(FakeLWE, object(), "bdd_hybrid", object(), "quantum", "enhanced", 512)

        classical = FakeLWE.calls[0][1]
        quantum = FakeLWE.calls[1][1]
        self.assertEqual(classical["deg_ring"], 512)
        self.assertTrue(classical["structure_leverage"])
        self.assertFalse(classical["mitm"])
        self.assertFalse(classical["babai"])
        self.assertNotIn("Grover", classical)
        self.assertTrue(quantum["Grover"])

    def test_standard_bdd_hybrid_omits_enhanced_only_arguments(self):
        run_lwe_attack(FakeLWE, object(), "bdd_hybrid", object(), "quantum", "standard", 512)
        kwargs = FakeLWE.calls[0][1]
        self.assertEqual(kwargs, {"red_cost_model": ANY, "mitm": False, "babai": False})

    def test_one_successful_attack_keeps_mode_usable(self):
        summary = summarize_attacks({
            "usvp": {"ok": False, "message": "timeout"},
            "dual_hybrid": {"ok": True, "rop_bits": 133.5},
            "bdd_hybrid": {"ok": False, "message": "unsupported"},
        })
        self.assertTrue(summary["ok"])
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["best_attack"], "dual_hybrid")
        self.assertEqual(summary["min_bits"], 133.5)
```

Import `ANY` directly from `unittest.mock` in the finished test so the equality assertion is readable.

- [ ] **Step 2: Run the runner tests and confirm the helper is absent**

Run:

```bash
python3 -m unittest tests.test_estimator_runner -v
```

Expected: FAIL because `run_lwe_attack` and `complete` do not exist.

- [ ] **Step 3: Implement the three-attack runner**

Add this dispatcher to `app/estimator_runner.py` and use it from `run_lwe()`:

```python
def run_lwe_attack(LWE, params, name: str, model, mode: str, profile: str, ring_degree: int):
    if name == "usvp":
        return LWE.primal_usvp(params, red_cost_model=model, red_shape_model="gsa")
    if name == "dual_hybrid":
        return LWE.dual_hybrid(params, red_cost_model=model)
    if name == "bdd_hybrid":
        kwargs = {"red_cost_model": model, "mitm": False, "babai": False}
        if profile == "enhanced":
            kwargs.update({"deg_ring": ring_degree, "structure_leverage": True})
            if mode == "quantum":
                kwargs["Grover"] = True
        return LWE.primal_hybrid(params, **kwargs)
    raise ValueError(f"Unsupported LWE attack: {name}")
```

Loop over `("usvp", "dual_hybrid", "bdd_hybrid")`. Change `summarize_attacks()` to set `complete` only when every attack succeeded. Set top-level `ok` when every model/mode has at least one successful attack and top-level `complete` only when every attack succeeded. Echo `estimator_profile`, `hard_problem_variant`, and `ring_degree` in `parameters` and at the response top level.

- [ ] **Step 4: Make remote workers select the profile root**

Validate `estimator_profile in {"standard", "enhanced"}` in `space_app.py`. Select paths exactly as follows:

```python
profile = str(payload.get("estimator_profile", "standard"))
path_name = "ENHANCED_LATTICE_ESTIMATOR_PATH" if profile == "enhanced" else "LATTICE_ESTIMATOR_PATH"
estimator_path = os.environ.get(path_name)
```

Clone the enhanced fork in both Dockerfiles and set:

```dockerfile
RUN git clone --depth=1 https://github.com/identitymapping/enhanced_lattice-estimator.git /opt/enhanced-lattice-estimator
ENV ENHANCED_LATTICE_ESTIMATOR_PATH=/opt/enhanced-lattice-estimator
```

- [ ] **Step 5: Verify runner and worker behavior**

Run:

```bash
python3 -m unittest tests.test_estimator_runner -v
python3 -m py_compile app/estimator_runner.py deploy/huggingface-estimator/space_app.py
```

Expected: all runner tests pass and compilation exits 0.

- [ ] **Step 6: Commit attack routing**

```bash
git add app/estimator_runner.py tests/test_estimator_runner.py deploy/huggingface-estimator/space_app.py deploy/huggingface-estimator/Dockerfile deploy/huggingface-live/Dockerfile
git commit -m "Route structured LWE attacks through enhanced estimator"
```

---

### Task 3: Shared Result Contract and Secret-Aware LWE Validation

**Files:**
- Create: `app/security_result.py`
- Modify: `app/parameter_search.py:104-185, 333-474, 700-885, 926-1000, 1247-1364`
- Test: `tests/test_parameter_search.py`

**Interfaces:**
- Consumes: `run_estimator()` and `estimator_profile_for()` from Task 1.
- Produces: `modulus_bits(q: int) -> int`.
- Produces: `selection_status(meets: bool) -> str`.
- Produces: `validation_result(...) -> dict` with the approved four states.
- Produces: `rotate_secret_candidates(candidates, rank_key) -> list[dict]`.
- Produces: top-level `validation` and candidate `selection.status`.

- [ ] **Step 1: Add failing contract, bit-width, scheduling, and target-state tests**

Add focused tests to `tests/test_parameter_search.py`:

```python
from app.security_result import modulus_bits, validation_result


def test_modulus_bits_uses_ceil_log2(self):
    self.assertEqual(modulus_bits(2048), 11)
    self.assertEqual(modulus_bits(2049), 12)
    self.assertEqual(modulus_bits(8192), 13)


def test_validation_contract_distinguishes_all_states(self):
    self.assertEqual(validation_result(False, "enhanced", 0, 0, 0, 8, True)["status"], "not_requested")
    self.assertEqual(validation_result(True, "enhanced", 2, 0, 0, 8, False)["status"], "failed")
    self.assertEqual(validation_result(True, "enhanced", 2, 2, 2, 8, True)["status"], "partial")
    self.assertEqual(validation_result(True, "enhanced", 8, 8, 8, 8, True)["status"], "validated")


def test_validation_scheduler_rotates_secret_distributions(self):
    request = parse_request({"secretDistribution": "auto", "errorDistribution": "auto"})
    candidates = build_candidates(request)[:300]
    scheduled = rotate_secret_candidates(candidates, lambda candidate: estimator_candidate_rank(candidate, request))
    first_secrets = [candidate["distribution"]["secret"]["name"] for candidate in scheduled[:3]]
    self.assertEqual(len(set(first_secrets)), 3)


def test_target_unmet_candidate_is_not_reported_ready(self):
    result = recommend_rlwe({
        "targetSecurity": 512,
        "minN": 512,
        "maxN": 512,
        "minQBits": 8,
        "maxQBits": 9,
        "useEstimator": False,
    })
    self.assertEqual(result["recommendation"]["selection"]["status"], "target_unmet")
    self.assertFalse(result["recommendation"]["selection"]["meets_target"])
    self.assertEqual(result["validation"]["status"], "not_requested")
```

Add a mocked `run_estimator` test that validates two candidates with different secret estimator descriptors and asserts `validation.status == "partial"`, `attempted_candidates == 2`, and `successful_candidates == 2`.

- [ ] **Step 2: Run the focused search tests and confirm contract failures**

Run:

```bash
python3 -m unittest tests.test_parameter_search -v
```

Expected: FAIL on the missing shared helpers and result fields.

- [ ] **Step 3: Implement the shared result helpers**

Create `app/security_result.py`:

```python
from __future__ import annotations

from typing import Any


def modulus_bits(q: int) -> int:
    if q < 2:
        raise ValueError("q must be at least 2.")
    return (q - 1).bit_length()


def selection_status(meets: bool) -> str:
    return "target_met" if meets else "target_unmet"


def validation_result(
    requested: bool,
    profile: str,
    attempted: int,
    successful: int,
    covered: int,
    eligible: int,
    attacks_complete: bool,
    estimator_commit: str | None = None,
    message_codes: list[str] | None = None,
) -> dict[str, Any]:
    if not requested:
        status = "not_requested"
    elif successful == 0:
        status = "failed"
    elif covered == eligible and successful == attempted == eligible and attacks_complete:
        status = "validated"
    else:
        status = "partial"
    return {
        "requested": requested,
        "status": status,
        "profile": profile,
        "estimator_commit": estimator_commit,
        "attempted_candidates": attempted,
        "successful_candidates": successful,
        "covered_candidates": covered,
        "eligible_candidates": eligible,
        "message_codes": list(dict.fromkeys(message_codes or [])),
    }
```

- [ ] **Step 4: Rotate validation across distinct secret descriptors**

Add this scheduler to `app/parameter_search.py`:

```python
def secret_validation_key(candidate: dict[str, Any]) -> str:
    estimator = candidate["distribution"]["secret"]["estimator"]
    return json.dumps(estimator, sort_keys=True, separators=(",", ":"))


def rotate_secret_candidates(candidates, rank_key):
    buckets: dict[str, list[dict[str, Any]]] = {}
    for candidate in sorted(candidates, key=rank_key):
        buckets.setdefault(secret_validation_key(candidate), []).append(candidate)
    ordered: list[dict[str, Any]] = []
    while buckets:
        for key in list(buckets):
            ordered.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
    return ordered
```

Do not add a secret-standard-deviation hardness formula. Keep the fast screen based on `Xe`; after estimator validation, rank validated `Xs/Xe` pairs by target satisfaction, measured security, dimensions/modulus, then sampling and standard-deviation tie-breakers.

- [ ] **Step 5: Replace silent fallback with explicit validation and selection states**

Route the payload through the selected profile:

```python
profile = estimator_profile_for(request.hard_problem_category, request.hard_problem_variant)
payload.update({
    "hard_problem_variant": request.hard_problem_variant,
    "ring_degree": candidate["ring"]["n"],
})
result = run_estimator(payload, timeout, config, profile)
```

Track attempted, successful, covered descriptor pairs, attack completeness, commit, and message codes. If no estimator result succeeds, keep the fast-screen candidate but return `validation.status="failed"`. If a finite budget covers only part of the pool, return `partial`. Add `selection.status` whenever candidate selection fields are created or recalculated.

Add stable machine-readable fields alongside the existing English fallback text:

```python
candidate["security"]["source_code"] = (
    f"sage_{profile}" if candidate["security"]["source"].startswith("sage-") else "fast_screen"
)
candidate["warning_codes"] = list(dict.fromkeys(candidate.get("warning_codes", []) + ["screen_scheme_not_bound"]))
result["next_step_code"] = "bind_scheme_constraints"
```

When validation succeeds, append `validation_applied`; when a profile path or Sage is unavailable, append `validation_config_missing`; when usable attacks are incomplete, append `validation_partial_attacks`. Preserve the raw estimator `message` in the validation entry so an unknown error remains inspectable.

Replace every security/filter/display use of `q.bit_length()` with `modulus_bits(q)`, including prime filtering and compactness scoring.

- [ ] **Step 6: Verify LWE search behavior**

Run:

```bash
python3 -m unittest tests.test_parameter_search tests.test_agent_config tests.test_estimator_runner -v
```

Expected: all tests pass; mocked RLWE requests use `enhanced`, mocked LWE/LWR requests use `standard`, and target-unmet responses remain successful analytical responses.

- [ ] **Step 7: Commit the LWE result contract**

```bash
git add app/security_result.py app/parameter_search.py tests/test_parameter_search.py
git commit -m "Report honest LWE validation states"
```

---

### Task 4: NTRU Family Corrections and Streamlined NTRU Prime

**Files:**
- Modify: `app/ntru_search.py:36-443, 446-618`
- Test: `tests/test_ntru_search.py`

**Interfaces:**
- Consumes: `modulus_bits`, `selection_status`, `validation_result`, and `run_estimator` from Tasks 1 and 3.
- Produces: NTRU ring families `power2`, `hps`, `hrss`, and `ntru_prime`.
- Produces: every NTRU ring object has `family_id`, `family`, `n`, `cyclotomic_index`, `polynomial`, `quotient`, `ntru_type`, and `preset` with nullable non-applicable fields.
- Produces: six official SNTRUP presets and reference-screen classical/quantum metadata.

- [ ] **Step 1: Add failing family and status tests**

Add to `tests/test_ntru_search.py`:

```python
def test_hps_and_hrss_are_always_circulant(self):
    for family in ("hps", "hrss"):
        result = recommend_ntru({
            "ringFamily": family,
            "hardProblemCategory": "ntru",
            "hardProblemVariant": "matrix",
            "targetSecurity": 128,
        })
        self.assertEqual(result["recommendation"]["ring"]["ntru_type"], "circulant")


def test_all_official_sntrup_presets_are_available(self):
    request = parse_ntru_request({
        "ringFamily": "ntru_prime",
        "minN": 1,
        "maxN": 2000,
        "minQBits": 2,
        "maxQBits": 24,
    })
    specs = ntru_candidate_specs(request)
    self.assertEqual(
        [(spec.preset, spec.n, spec.q, spec.fixed_weight) for spec in specs],
        [
            ("sntrup653", 653, 4621, 288),
            ("sntrup761", 761, 4591, 286),
            ("sntrup857", 857, 5167, 322),
            ("sntrup953", 953, 6343, 396),
            ("sntrup1013", 1013, 7177, 448),
            ("sntrup1277", 1277, 7879, 492),
        ],
    )
    self.assertTrue(all(spec.polynomial == f"x^{spec.n} - x - 1" for spec in specs))
    self.assertTrue(all(spec.ntru_type == "circulant" for spec in specs))


def test_ntru_prime_candidate_has_field_aware_common_contract(self):
    result = recommend_ntru({"ringFamily": "ntru_prime", "targetSecurity": 128})
    candidate = result["recommendation"]
    self.assertIsNone(candidate["ring"]["cyclotomic_index"])
    self.assertEqual(candidate["ring"]["preset"], "sntrup653")
    self.assertEqual(candidate["modulus"]["bits"], 13)
    self.assertIn(candidate["selection"]["status"], {"target_met", "target_unmet"})
    self.assertEqual(result["validation"]["status"], "not_requested")


def test_ntru_target_unmet_is_explicit(self):
    result = recommend_ntru({"ringFamily": "power2", "targetSecurity": 256})
    self.assertEqual(result["recommendation"]["selection"]["status"], "target_unmet")
    self.assertFalse(result["recommendation"]["selection"]["meets_target"])
```

- [ ] **Step 2: Run NTRU tests and confirm current family failures**

Run:

```bash
python3 -m unittest tests.test_ntru_search -v
```

Expected: FAIL because HPS/HRSS specs are matrix, `ntru_prime` is unsupported, fields are missing, and statuses are absent.

- [ ] **Step 3: Add exact SNTRUP preset data**

Extend `NTRUCandidateSpec` with `preset: str | None`, `fixed_weight: int | None`, `screen_quantum_bits: float | None`, and `nist_category: int | None`. Add the official rows:

```python
SNTRUP_ROWS = (
    ("sntrup653", 653, 4621, 288, 129.0, 117.0, 1),
    ("sntrup761", 761, 4591, 286, 153.0, 139.0, 2),
    ("sntrup857", 857, 5167, 322, 175.0, 159.0, 3),
    ("sntrup953", 953, 6343, 396, 196.0, 178.0, 4),
    ("sntrup1013", 1013, 7177, 448, 209.0, 190.0, 4),
    ("sntrup1277", 1277, 7879, 492, 270.0, 245.0, 5),
)
```

The screen values are the minimum including-hybrid pre/post-quantum values in the official Round-3 estimate table. Build each preset with:

```python
NTRUCandidateSpec(
    family_id="ntru_prime",
    preset=name,
    n=n,
    q=q,
    polynomial=f"x^{n} - x - 1",
    quotient=f"Z_{q}[x] / (x^{n} - x - 1)",
    ntru_type="circulant",
    secret_distribution=sparse_ternary_distribution(weight // 2, weight // 2, n),
    error_distribution=uniform_mod_distribution(3),
    fixed_weight=weight,
    screen_bits=classical_bits,
    screen_quantum_bits=quantum_bits,
    screen_attack="official-including-hybrid-minimum",
    nist_category=category,
    note="Streamlined NTRU Prime Round-3 preset; fixed-weight signs use a balanced estimator approximation.",
)
```

- [ ] **Step 4: Fix NTRU type and common fields**

Set `ntru_type="circulant"` in HPS and HRSS specs. Change type selection to:

```python
def estimator_ntru_type(request: NTRURequest, spec: NTRUCandidateSpec) -> str:
    if spec.family_id != "power2":
        return "circulant"
    return "matrix" if request.hard_problem_variant == "matrix" else "circulant"
```

Set `cyclotomic_index=None` for NTRU Prime and HPS/HRSS representations where a cyclotomic index is not part of the displayed model. Return nullable NTT fields instead of omitting keys. Use `modulus_bits()` for filtering and output.

- [ ] **Step 5: Apply the shared validation contract**

Replace the duplicated subprocess code with:

```python
result = run_estimator(payload, timeout, config, "standard")
```

Use the same `validation_result()` rules as LWE. Preserve partial model results rather than requiring all four model/mode values. For each available model/mode minimum, update that field; retain `None` only for unavailable values. Add `selection.status` after every selection recalculation.

Set `security.source_code="ntru_reference_screen"` before live validation and `security.source_code="sage_standard"` after a usable estimator result. Return `next_step_code="bind_scheme_constraints"`, candidate `warning_codes`, and validation message codes using the same names established in Task 3.

- [ ] **Step 6: Verify all NTRU families and result states**

Run:

```bash
python3 -m unittest tests.test_ntru_search tests.test_parameter_search -v
```

Expected: all tests pass; HPS/HRSS/NTRU Prime are circulant, power2 still honors matrix/ring, and all six SNTRUP rows are available.

- [ ] **Step 7: Commit NTRU Prime and corrected families**

```bash
git add app/ntru_search.py tests/test_ntru_search.py
git commit -m "Add Streamlined NTRU Prime presets"
```

---

### Task 5: Polynomial-Ring Reduction Profiles

**Files:**
- Create: `app/polynomial_ring.py`
- Create: `tests/test_polynomial_ring.py`

**Interfaces:**
- Produces: `SUPPORTED_RING_TYPES = {"cyclic", "negacyclic", "ntru_prime"}`.
- Produces: `ring_polynomial(n: int, ring_type: str) -> str`.
- Produces: `reduction_targets(raw_degree: int, n: int, ring_type: str) -> tuple[tuple[int, int], ...]`.
- Produces: `coefficient_profiles(n: int, ring_type: str) -> tuple[CoefficientProfile, ...]`.
- Produces: `CoefficientProfile(positive_terms: int, negative_terms: int)`.

- [ ] **Step 1: Write brute-force small-ring tests**

Create `tests/test_polynomial_ring.py` with independent reference multiplication:

```python
import unittest

from app.polynomial_ring import coefficient_profiles, reduction_targets, ring_polynomial


def multiply(left, right, ring_type):
    n = len(left)
    raw = [0] * (2 * n - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            raw[i + j] += a * b
    result = [0] * n
    for degree, value in enumerate(raw):
        if degree < n:
            result[degree] += value
        elif ring_type == "cyclic":
            result[degree - n] += value
        elif ring_type == "negacyclic":
            result[degree - n] -= value
        else:
            result[degree - n] += value
            result[degree - n + 1] += value
    return result


class PolynomialRingTests(unittest.TestCase):
    def test_reduction_targets_reconstruct_all_three_products(self):
        left = [2, -1, 3, 1]
        right = [-1, 2, 0, 4]
        for ring_type in ("cyclic", "negacyclic", "ntru_prime"):
            raw = [0] * 7
            for i, a in enumerate(left):
                for j, b in enumerate(right):
                    raw[i + j] += a * b
            actual = [0] * 4
            for degree, value in enumerate(raw):
                for output, sign in reduction_targets(degree, 4, ring_type):
                    actual[output] += sign * value
            self.assertEqual(actual, multiply(left, right, ring_type))

    def test_profile_counts_match_all_one_products(self):
        self.assertEqual(
            [(p.positive_terms, p.negative_terms) for p in coefficient_profiles(4, "cyclic")],
            [(4, 0)] * 4,
        )
        self.assertEqual(
            [(p.positive_terms, p.negative_terms) for p in coefficient_profiles(4, "negacyclic")],
            [(1, 3), (2, 2), (3, 1), (4, 0)],
        )
        self.assertEqual(sum(p.positive_terms for p in coefficient_profiles(4, "ntru_prime")), 22)

    def test_ring_names_and_validation(self):
        self.assertEqual(ring_polynomial(7, "cyclic"), "x^7 - 1")
        self.assertEqual(ring_polynomial(7, "negacyclic"), "x^7 + 1")
        self.assertEqual(ring_polynomial(7, "ntru_prime"), "x^7 - x - 1")
        with self.assertRaisesRegex(ValueError, "ring_type"):
            coefficient_profiles(4, "unknown")
```

- [ ] **Step 2: Run the new ring tests and confirm the module is absent**

Run:

```bash
python3 -m unittest tests.test_polynomial_ring -v
```

Expected: FAIL because `app.polynomial_ring` does not exist.

- [ ] **Step 3: Implement degree reduction and multiplicity profiles**

Create `app/polynomial_ring.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_RING_TYPES = {"cyclic", "negacyclic", "ntru_prime"}


@dataclass(frozen=True)
class CoefficientProfile:
    positive_terms: int
    negative_terms: int


def validate_ring(n: int, ring_type: str) -> None:
    if n < 1:
        raise ValueError("n must be at least 1.")
    if ring_type not in SUPPORTED_RING_TYPES:
        raise ValueError("ring_type must be one of cyclic, negacyclic, ntru_prime.")


def ring_polynomial(n: int, ring_type: str) -> str:
    validate_ring(n, ring_type)
    if ring_type == "cyclic":
        return f"x^{n} - 1"
    if ring_type == "negacyclic":
        return f"x^{n} + 1"
    return f"x^{n} - x - 1"


def reduction_targets(raw_degree: int, n: int, ring_type: str) -> tuple[tuple[int, int], ...]:
    validate_ring(n, ring_type)
    if raw_degree < 0 or raw_degree > 2 * n - 2:
        raise ValueError("raw_degree must be between 0 and 2*n-2.")
    if raw_degree < n:
        return ((raw_degree, 1),)
    output = raw_degree - n
    if ring_type == "cyclic":
        return ((output, 1),)
    if ring_type == "negacyclic":
        return ((output, -1),)
    return ((output, 1), (output + 1, 1))


def raw_product_multiplicity(raw_degree: int, n: int) -> int:
    return raw_degree + 1 if raw_degree < n else 2 * n - 1 - raw_degree


def coefficient_profiles(n: int, ring_type: str) -> tuple[CoefficientProfile, ...]:
    validate_ring(n, ring_type)
    positive = [0] * n
    negative = [0] * n
    for degree in range(2 * n - 1):
        multiplicity = raw_product_multiplicity(degree, n)
        for output, sign in reduction_targets(degree, n, ring_type):
            if sign == 1:
                positive[output] += multiplicity
            else:
                negative[output] += multiplicity
    return tuple(CoefficientProfile(positive[i], negative[i]) for i in range(n))
```

- [ ] **Step 4: Verify reduction math**

Run:

```bash
python3 -m unittest tests.test_polynomial_ring -v
```

Expected: all three ring products and profile checks pass.

- [ ] **Step 5: Commit the ring module**

```bash
git add app/polynomial_ring.py tests/test_polynomial_ring.py
git commit -m "Model NTRU polynomial ring reductions"
```

---

### Task 6: Ring-Aware NTRU DFR and Per-Coefficient Union Bound

**Files:**
- Modify: `app/decryption_failure.py:29-191, 367-571`
- Test: `tests/test_decryption_failure.py`

**Interfaces:**
- Consumes: `coefficient_profiles()` and `ring_polynomial()` from Task 5.
- Produces: `ring_product_coefficient_pmfs(left, right, n, ring_type) -> tuple[PMF, ...]`.
- Produces: NTRU DFR fields `ring_type`, `ring_polynomial`, `single_coefficient_semantics`, and `coefficient_dfr`.
- Preserves: all existing LWE DFR fields and semantics.

- [ ] **Step 1: Add failing ring-aware DFR tests**

Add to `tests/test_decryption_failure.py`:

```python
def ntru_product_payload(ring_type):
    bernoulli = {"type": "custom_pmf", "pmf": {"0": "0.5", "1": "0.5"}}
    return {
        "type": "ntru",
        "ringType": ring_type,
        "n": 3,
        "p0": 1,
        "p1": 0,
        "p2": 0,
        "p3": 0,
        "delta": 1,
        "g": bernoulli,
        "s": bernoulli,
        "f": ZERO,
        "e": ZERO,
        "m": ZERO,
    }


def test_ntru_reports_ring_and_worst_coefficient_metadata(self):
    result = calculate_decryption_failure(ntru_product_payload("negacyclic"))
    self.assertEqual(result["ring_type"], "negacyclic")
    self.assertEqual(result["ring_polynomial"], "x^3 + 1")
    self.assertEqual(result["single_coefficient_semantics"], "worst_coefficient")
    self.assertIn(result["coefficient_dfr"]["worst_index"], range(3))
    self.assertEqual(result["coefficient_dfr"]["distinct_profiles"], 3)


def test_vector_dfr_sums_each_coefficient_failure(self):
    result = calculate_decryption_failure(ntru_product_payload("ntru_prime"))
    coefficient_failures = [Decimal(value) for value in result["coefficient_dfr"]["failure_probabilities"]]
    expected = min(Decimal(1), sum(coefficient_failures))
    self.assertEqual(Decimal(result["vector_failure_probability_before_ecc"]), expected)
    self.assertEqual(
        Decimal(result["single_coefficient_failure_probability"]),
        max(coefficient_failures),
    )
    self.assertIn("ntru_prime_coefficient_marginal", result["warning_codes"])


def test_invalid_ntru_ring_type_is_rejected(self):
    with self.assertRaisesRegex(ValueError, "ring_type"):
        calculate_decryption_failure(ntru_product_payload("ordinary"))


def test_lwe_union_bound_contract_is_unchanged(self):
    result = calculate_decryption_failure({
        "type": "lwe", "m": 1, "n": 2, "delta": 0,
        "s": ZERO, "e": ZERO, "e1": ZERO, "r": ZERO,
        "ec1": ZERO, "ec2": ZERO,
        "e2": {"type": "custom_pmf", "pmf": {"0": "0.75", "1": "0.25"}},
    })
    self.assertEqual(result["single_coefficient_semantics"], "identical_coefficient_model")
    self.assertEqual(Decimal(result["vector_failure_probability_before_ecc"]), Decimal("0.5"))
```

- [ ] **Step 2: Run DFR tests and confirm missing ring fields**

Run:

```bash
python3 -m unittest tests.test_decryption_failure -v
```

Expected: existing tests pass, new ring-aware tests fail.

- [ ] **Step 3: Build coefficient PMFs from cached profiles**

Add:

```python
def ring_product_coefficient_pmfs(left: PMF, right: PMF, n: int, ring_type: str) -> tuple[PMF, ...]:
    product = multiply_pmfs(left, right)
    negative_product = scale_pmf(product, Decimal(-1))
    cache: dict[tuple[int, int], PMF] = {}
    results = []
    for profile in coefficient_profiles(n, ring_type):
        key = (profile.positive_terms, profile.negative_terms)
        if key not in cache:
            cache[key] = add_pmfs(
                convolve_power(product, profile.positive_terms),
                convolve_power(negative_product, profile.negative_terms),
            )
        results.append(cache[key])
    return tuple(results)


def scaled_ring_products(scale: Decimal, left: PMF, right: PMF, n: int, ring_type: str) -> tuple[PMF, ...]:
    if scale == 0:
        return tuple(zero_pmf() for _ in range(n))
    return tuple(scale_pmf(pmf, scale) for pmf in ring_product_coefficient_pmfs(left, right, n, ring_type))
```

In `calculate_ntru()`, parse `ringType`/`ring_type`, build the three product-term tuples, and construct one total PMF per output coefficient. Add `p3*e` to every coefficient.

Pass the first coefficient PMF as the compatibility `error` argument and the complete tuple as `coefficient_errors`; `result_payload()` selects the actual worst coefficient before reporting support and failure values.

- [ ] **Step 4: Generalize result aggregation without changing LWE**

Change `result_payload()` to accept `coefficient_errors: tuple[PMF, ...] | None` and `distinct_profiles: int | None`. Use:

```python
errors = coefficient_errors or (error,) * vector_dimension
failures = [
    sum(probability for value, probability in item.probabilities.items() if abs(value) > delta)
    for item in errors
]
worst_index = max(range(len(failures)), key=failures.__getitem__)
single_failure = failures[worst_index]
vector_failure = min(Decimal(1), sum(failures, Decimal(0)))
```

For NTRU return the complete coefficient failure list as decimal strings for auditability. For LWE retain one modeled coefficient, `identical_coefficient_model`, and multiply its failure by `n`. Deduplicate warnings and add stable codes:

```python
warning_codes = ["dfr_union_bound"]
if error.tail_bound:
    warning_codes.append("dfr_gaussian_tail_excluded")
if ring_type == "ntru_prime":
    warning_codes.append("ntru_prime_coefficient_marginal")
```

Map the existing sparse fixed-weight PMF warning to `dfr_sparse_fixed_weight_marginal`, and return `error_correction.code="dfr_ecc_external"`. The human-readable warning and note fields remain for compatibility.

Keep English fallback warnings and `error_correction.note`; add `error_correction.code="dfr_ecc_external"`.

- [ ] **Step 5: Run mathematical and regression tests**

Run:

```bash
python3 -m unittest tests.test_polynomial_ring tests.test_decryption_failure -v
```

Expected: all ring, Kyber, custom PMF, Gaussian-tail, boundary, and existing LWE tests pass.

- [ ] **Step 6: Commit ring-aware DFR**

```bash
git add app/decryption_failure.py tests/test_decryption_failure.py
git commit -m "Compute NTRU DFR by polynomial ring"
```

---

### Task 7: Testable Browser Model, Dynamic Ring Controls, and Stale Requests

**Files:**
- Create: `static/app-model.js`
- Create: `tests/js/app-model.test.cjs`
- Modify: `static/index.html:40-179, 326-328`
- Modify: `static/app.js:1-103, 405-543, 830-885, 1008-1051`
- Modify: `app/server.py:124-135`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `EasyLatticeModel.ringOptions(category)`.
- Produces: `EasyLatticeModel.normalizeRingSelection(category, family, variant)`.
- Produces: `EasyLatticeModel.resultPresentation(validationStatus, selectionStatus)`.
- Produces: `EasyLatticeModel.compactRows(rows)`.
- Produces: `EasyLatticeModel.nextRevision()` and `acceptsResponse()`.
- Consumes: Task 3/4 response `validation.status` and `selection.status`.

- [ ] **Step 1: Write DOM-free frontend tests**

Create `tests/js/app-model.test.cjs`:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const model = require("../../static/app-model.js");

test("ring options follow the hard problem", () => {
  assert.deepEqual(model.ringOptions("lwe").map((item) => item.value), ["power2", "ternary"]);
  assert.deepEqual(
    model.ringOptions("ntru").map((item) => item.value),
    ["power2", "hps", "hrss", "ntru_prime"],
  );
});

test("classic and prime NTRU families force the ring variant", () => {
  for (const family of ["hps", "hrss", "ntru_prime"]) {
    assert.deepEqual(model.normalizeRingSelection("ntru", family, "matrix"), {
      family,
      variant: "ring",
      matrixAllowed: false,
    });
  }
  assert.equal(model.normalizeRingSelection("ntru", "power2", "matrix").variant, "matrix");
});

test("status presentation never labels partial failed or unmet as ready", () => {
  assert.deepEqual(model.resultPresentation("validated", "target_met"), { kind: "done", key: "statusReady" });
  assert.equal(model.resultPresentation("partial", "target_met").key, "statusPartial");
  assert.equal(model.resultPresentation("failed", "target_met").key, "statusValidationFailed");
  assert.equal(model.resultPresentation("validated", "target_unmet").key, "statusTargetUnmet");
  assert.equal(model.resultPresentation("not_requested", "target_met").key, "statusScreened");
});

test("rows omit null undefined and empty values", () => {
  assert.deepEqual(model.compactRows([["a", 0], ["b", null], ["c", undefined], ["d", ""]]), [["a", 0]]);
});

test("responses are accepted only for the current form revision", () => {
  assert.equal(model.acceptsResponse(3, 3), true);
  assert.equal(model.acceptsResponse(2, 3), false);
});
```

- [ ] **Step 2: Run Node tests and confirm the model is absent**

Run:

```bash
node --test tests/js/app-model.test.cjs
```

Expected: FAIL because `static/app-model.js` does not exist.

- [ ] **Step 3: Implement the UMD browser model**

Create `static/app-model.js`:

```javascript
(function expose(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.EasyLatticeModel = api;
})(typeof globalThis === "object" ? globalThis : this, function buildModel() {
  const LWE_RINGS = [
    { value: "power2", label: "x^n + 1" },
    { value: "ternary", label: "x^n - x^(n/2) + 1" },
  ];
  const NTRU_RINGS = [
    { value: "power2", label: "x^n + 1" },
    { value: "hps", label: "NTRU-HPS: x^N - 1" },
    { value: "hrss", label: "NTRU-HRSS: x^N - 1" },
    { value: "ntru_prime", label: "Streamlined NTRU Prime: x^n - x - 1" },
  ];
  const FORCED_RING_FAMILIES = new Set(["hps", "hrss", "ntru_prime"]);

  function ringOptions(category) {
    return (category === "ntru" ? NTRU_RINGS : LWE_RINGS).map((item) => ({ ...item }));
  }

  function normalizeRingSelection(category, family, variant) {
    const matrixAllowed = category === "ntru" && !FORCED_RING_FAMILIES.has(family);
    return { family, variant: matrixAllowed ? variant : category === "ntru" ? "ring" : variant, matrixAllowed };
  }

  function resultPresentation(validationStatus, selectionStatus) {
    if (selectionStatus === "target_unmet") return { kind: "warning", key: "statusTargetUnmet" };
    if (validationStatus === "failed") return { kind: "error", key: "statusValidationFailed" };
    if (validationStatus === "partial") return { kind: "warning", key: "statusPartial" };
    if (validationStatus === "validated") return { kind: "done", key: "statusReady" };
    return { kind: "screened", key: "statusScreened" };
  }

  function compactRows(rows) {
    return rows.filter(([, value]) => value !== null && value !== undefined && value !== "");
  }

  function nextRevision(current) {
    return current + 1;
  }

  function acceptsResponse(startedRevision, currentRevision) {
    return startedRevision === currentRevision;
  }

  return { ringOptions, normalizeRingSelection, resultPresentation, compactRows, nextRevision, acceptsResponse };
});
```

- [ ] **Step 4: Add dynamic security and DFR ring controls**

Load `app-model.js` before `app.js`. Rebuild `#ring-family` when the hard-problem radio changes. For HPS, HRSS, and NTRU Prime, check `ntru:ring`, disable `ntru:matrix`, and ensure the payload cannot contain matrix. Re-enable matrix for power2 NTRU.

Add this NTRU DFR field to `static/index.html`:

```html
<label>
  <span data-i18n="dfrRingType">Polynomial ring</span>
  <select name="dfrRingType">
    <option value="cyclic">x^n - 1</option>
    <option value="negacyclic">x^n + 1</option>
    <option value="ntru_prime">x^n - x - 1</option>
  </select>
</label>
```

Include `ringType: data.get("dfrRingType")` in NTRU DFR payloads.

- [ ] **Step 5: Gate requests by form revision**

Maintain separate search and DFR revisions and in-flight flags:

```javascript
let searchRevision = 0;
let dfrRevision = 0;
let searchResultRevision = null;
let dfrResultRevision = null;
let searchInFlight = false;
let dfrInFlight = false;
```

Every effective `input` or `change` increments the matching revision, marks an existing result stale, disables its copy button, and shows `statusInputsChanged`. Language switching does not increment either revision. At request start, capture the current revision and disable that form's submit button. In `finally`, re-enable it. Before accepting success or failure UI updates, require `EasyLatticeModel.acceptsResponse(startedRevision, currentRevision)`.

- [ ] **Step 6: Serve and verify the shared model**

Add `/app-model.js` to `EasyLatticeHandler.do_GET()` and to the expected assets in `tests/test_server.py`.

Run:

```bash
node --test tests/js/app-model.test.cjs
node --check static/app-model.js
node --check static/app.js
python3 -m unittest tests.test_server -v
```

Expected: all Node and server tests pass.

- [ ] **Step 7: Commit browser controls and stale handling**

```bash
git add static/app-model.js static/index.html static/app.js tests/js/app-model.test.cjs app/server.py tests/test_server.py
git commit -m "Add dynamic ring and stale result state"
```

---

### Task 8: Field-Aware Bilingual Rendering and Preview Contract

**Files:**
- Modify: `static/app.js:104-403, 545-617, 672-827, 1099-1245`
- Modify: `static/preview-data.js`
- Modify: `static/styles.css`

**Interfaces:**
- Consumes: backend `warning_codes`, `source_code`, `next_step_code`, `validation`, `selection.status`, and DFR ring metadata.
- Consumes: `EasyLatticeModel.compactRows()` and `resultPresentation()` from Task 7.
- Produces: no visible `undefined`, truthful status pills, localized known messages, and raw unknown estimator failures.

- [ ] **Step 1: Add translation keys and code maps**

Add English and Chinese translations for these exact keys:

```javascript
const RESULT_CODE_KEYS = {
  screen_scheme_not_bound: "warningScreenSchemeNotBound",
  validation_applied: "warningValidationApplied",
  validation_config_missing: "warningValidationConfigMissing",
  validation_partial_attacks: "warningValidationPartialAttacks",
  ntru_prime_coefficient_marginal: "warningNtruPrimeMarginal",
  dfr_union_bound: "dfrWarningUnionBound",
  dfr_gaussian_tail_excluded: "dfrWarningTail",
  dfr_sparse_fixed_weight_marginal: "dfrWarningSparse",
  dfr_ecc_external: "dfrEccExternal",
  bind_scheme_constraints: "nextBindSchemeConstraints",
};
```

Also add `statusScreened`, `statusPartial`, `statusValidationFailed`, `statusTargetUnmet`, `statusInputsChanged`, `validationStatus`, `estimatorProfile`, `dfrRingType`, `worstCoefficient`, and `distinctCoefficientProfiles` in both languages.

- [ ] **Step 2: Render optional fields and result states safely**

Build instance/security rows, then filter with `EasyLatticeModel.compactRows()`. Construct cyclotomic text only when `candidate.ring.cyclotomic_index != null`; omit NTT/factorization rows when null. Use:

```javascript
const presentation = EasyLatticeModel.resultPresentation(
  result.validation?.status || "not_requested",
  candidate.selection?.status || (candidate.selection?.meets_target ? "target_met" : "target_unmet"),
);
setStatus(presentation.kind, t(presentation.key));
```

Render validation profile, revision, attempted/success/coverage counts, and status. Localize known codes through `RESULT_CODE_KEYS`; append any raw `message` or estimator error that has no known code.

- [ ] **Step 3: Render ring-aware DFR metadata**

For NTRU DFR add rows for polynomial ring, worst coefficient index, and distinct coefficient profiles. Continue formatting `log2 DFR` to exactly two decimal places. Localize DFR warnings from `warning_codes`, falling back to current English string matching for old API responses.

- [ ] **Step 4: Refresh preview fixtures**

Update every preview recommendation with:

```javascript
validation: {
  requested: false,
  status: "not_requested",
  profile: "enhanced",
  estimator_commit: null,
  attempted_candidates: 0,
  successful_candidates: 0,
  covered_candidates: 0,
  eligible_candidates: 7168,
  message_codes: [],
}
```

Add `selection.status`. Make `withSelection()` derive `target_met`/`target_unmet`. When preview `useEstimator` is checked, return `validation.status="failed"` with `message_codes=["validation_config_missing"]` so the static page demonstrates that it has no live estimator.

Add all DFR fields from Task 6. Keep the default NTRU fixture cyclic, and expose preview clones for negacyclic and NTRU Prime based on the selected DFR ring value so all three ring descriptions can be inspected.

- [ ] **Step 5: Add restrained stale/disabled styling**

Use existing colors and spacing. Add selectors for `.status-pill.screened`, `.status-pill.warning`, disabled copy/submit buttons, and a stale result opacity treatment that leaves values readable. Do not add new cards or decorative elements.

- [ ] **Step 6: Run static and frontend tests**

Run:

```bash
node --test tests/js/app-model.test.cjs
node --check static/app-model.js
node --check static/app.js
node --check static/preview-data.js
rg -n "undefined" static/preview-data.js static/app.js
```

Expected: tests and syntax checks pass; `undefined` appears only in defensive JavaScript comparisons or optional payload omission, never in fixture display strings.

- [ ] **Step 7: Commit rendering and preview updates**

```bash
git add static/app.js static/preview-data.js static/styles.css
git commit -m "Render validation and DFR states bilingually"
```

---

### Task 9: Documentation, Full Regression, and Browser Verification

**Files:**
- Modify: `README.md`
- Modify: `README.zh.md`
- Modify: `docs/architecture.md`
- Modify: `deploy/huggingface-estimator/README.md`
- Modify: `deploy/huggingface-live/README.md`

**Interfaces:**
- Documents: both local estimator roots, exact routing table, result-state meanings, SNTRUP presets, ring-aware DFR approximation, union-bound semantics, and stale-result behavior.

- [ ] **Step 1: Update English documentation**

In `README.md`, document:

```json
"estimator": {
  "sage_binary": "sage",
  "lattice_estimator_path": "/path/to/malb/lattice-estimator",
  "enhanced_lattice_estimator_path": "/path/to/identitymapping/enhanced-lattice-estimator"
}
```

State the exact routing table and attack set. Explain that `validated` means complete eligible-pool coverage, `partial` means only the validated subset is ranked, `failed` retains only a visibly labeled fast screen, and `target_unmet` is analytical output rather than a recommendation that met the request.

Document six SNTRUP presets and distinguish security classification as circulant from DFR reduction over `x^n-x-1`. Document worst-coefficient single DFR and the sum of coefficient failure probabilities as the vector union bound.

- [ ] **Step 2: Mirror the documentation in Chinese**

Update the corresponding sections in `README.zh.md` with the same commands, field names, routing, caveats, and numerical conventions. Keep the top language-switch links unchanged so the README remains one-click bilingual rather than mixed-language.

- [ ] **Step 3: Update architecture and deployment notes**

Add the estimator subprocess boundary and new modules to `docs/architecture.md`. Update both deployment READMEs with `ENHANCED_LATTICE_ESTIMATOR_PATH`, profile-aware payloads, and the fact that both packages share the import name `estimator` and are isolated by process.

- [ ] **Step 4: Run the complete automated suite**

Run:

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/app-model.test.cjs
python3 -m py_compile app/*.py deploy/huggingface-estimator/space_app.py
bash -n scripts/setup-local.sh
node --check static/app-model.js
node --check static/app.js
node --check static/preview-data.js
git diff --check
```

Expected: every Python and Node test passes; all syntax and whitespace checks exit 0.

- [ ] **Step 5: Start the local server and verify API contracts**

Run on an unused port:

```bash
HOST=127.0.0.1 PORT=8004 python3 -m app.server
```

Verify:

```bash
curl -s http://127.0.0.1:8004/api/config/public
curl -s -X POST http://127.0.0.1:8004/api/agent/recommend -H 'Content-Type: application/json' -d '{"problem":"ntru","hardProblemCategory":"ntru","hardProblemVariant":"ring","ringFamily":"ntru_prime","targetSecurity":128,"useEstimator":false}'
curl -s -X POST http://127.0.0.1:8004/api/decryption-failure/calculate -H 'Content-Type: application/json' -d '{"type":"ntru","ringType":"ntru_prime","n":2,"delta":0,"p0":0,"p1":0,"p2":0,"p3":1,"g":{"type":"custom_pmf","pmf":{"0":1}},"f":{"type":"custom_pmf","pmf":{"0":1}},"s":{"type":"custom_pmf","pmf":{"0":1}},"m":{"type":"custom_pmf","pmf":{"0":1}},"e":{"type":"custom_pmf","pmf":{"0":0.5,"1":0.5}}}'
```

Expected: public config reports both profiles; NTRU Prime reports circulant security classification and `x^n-x-1`; DFR reports NTRU Prime ring metadata, worst coefficient, warning code, and vector union bound.

- [ ] **Step 6: Perform desktop and mobile browser verification**

Use Playwright or the available browser harness at `http://127.0.0.1:8004/` and `http://127.0.0.1:8004/index.html?preview=1`. Capture screenshots at `1440x900` and `390x844` after checking:

- English and Chinese dynamic statuses and warnings.
- LWE ring options versus NTRU power2/HPS/HRSS/NTRU Prime options.
- Matrix disabled for HPS/HRSS/NTRU Prime and available for power2 NTRU.
- `target_unmet`, `partial`, `failed`, and screened states are never labeled Ready.
- NTRU result rows contain no visible `undefined`.
- Cyclic, negacyclic, and NTRU Prime DFR selections display the correct polynomial.
- Editing an effective input marks the result stale, disables copy, and ignores an older response.
- Language switching re-renders without marking results stale.
- Submit buttons stay stable in size and are disabled during active requests.
- No control or text overlap occurs at either viewport.

- [ ] **Step 7: Commit documentation and final verification fixes**

```bash
git add README.md README.zh.md docs/architecture.md deploy/huggingface-estimator/README.md deploy/huggingface-live/README.md
git commit -m "Document estimator and ring hardening"
```

- [ ] **Step 8: Inspect the final change set**

Run:

```bash
git status --short
git log --oneline --decorate -10
git diff origin/main...HEAD --stat
```

Expected: only intentional changes are present, the task commits are visible, and the final diff covers configuration, estimator execution, search contracts, NTRU Prime, DFR rings, browser behavior, tests, and bilingual documentation.
