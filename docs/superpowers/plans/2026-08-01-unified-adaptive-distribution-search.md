# Unified Adaptive Distribution Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make RLWE and NTRU use one adaptive minimum-parameter search, add independent pure/combination Secret and Error distributions, and expose progress, cancellation, and explicit no-feasible results.

**Architecture:** Add a shared distribution contract and a shared adaptive-validation helper. RLWE and NTRU keep scheme-specific candidate construction and estimator normalization but feed ordered lazy candidates into the helper. Estimator work is split into cancellable Sage tasks; the server exposes task progress, while the vanilla browser adds distribution controls, model explanations, and result states.

**Tech Stack:** Python 3.10 standard library, Sage/lattice-estimator subprocesses, `unittest`, vanilla JavaScript/HTML/CSS, and Node `node:test`.

## Global Constraints

- Secret and Error modes default to `pure`.
- `maxDistributionComponents` is an integer from 1 through 6 and defaults to 3.
- `combination` enumerates unordered additive CBD/sparse-ternary components independently for Secret and Error.
- LWR, RLWR, and MLWR Error is compression noise and rejects Error-combination requests.
- Candidate order is `n` ascending, then NTT-valid `q` ascending, then deterministic distribution order.
- The first measured candidate meeting the selected target is the minimum feasible result under that order.
- Local execution has no automatic candidate or wall-clock limit; cancellation is explicit. Remote timeout policy remains bounded.
- Every completed candidate preserves MATZOV classical/quantum and ADPS16 classical/quantum results.
- Composite estimator output is a `composite_moment` approximation with a visible warning.
- MATZOV labels mention polynomial/sub-exponential terms and “more aggressive”; ADPS16 labels mention the exponential term and “more conservative” in English and Chinese.
- Existing request and result fields remain backward compatible; new fields are additive.

---

### Task 1: Add the shared distribution contract and bounded enumerator

**Files:**
- Create: `app/distribution_search.py`
- Modify: `app/parameter_search.py` (request parsing and distribution candidate generation)
- Modify: `app/ntru_search.py` (request parsing and preset distribution generation)
- Test: `tests/test_distribution_search.py`, `tests/test_parameter_search.py`, `tests/test_ntru_search.py`

**Interfaces:**
- `DistributionRequest(mode: str, selector: str, max_components: int)`.
- `parse_distribution_request(raw, prefix, *, lwr_error=False) -> DistributionRequest`.
- `enumerate_distribution_candidates(n, request) -> Iterator[dict[str, Any]]`.
- `compose_distribution(components) -> dict[str, Any]`.
- `distribution_order_key(distribution) -> tuple[Any, ...]`.

- [ ] **Step 1: Write failing tests for defaults, limits, and composition.**

```python
class DistributionSearchTests(unittest.TestCase):
    def test_distribution_defaults_are_pure_and_bounded(self):
        secret = parse_distribution_request({}, "secret")
        self.assertEqual((secret.mode, secret.selector, secret.max_components), ("pure", "auto", 3))

    def test_composition_sums_variance_and_support(self):
        result = compose_distribution([centered_binomial_component(2), sparse_ternary_component(2, 2, 512)])
        self.assertEqual(result["family"], "composite")
        self.assertEqual(result["component_count"], 2)
        self.assertEqual(result["estimator"]["type"], "composite_moment")

    def test_component_limit_is_one_through_six(self):
        for value in (0, 7, "three"):
            with self.assertRaises(ValueError):
                parse_distribution_request({"secretDistributionMode": "combination", "maxDistributionComponents": value}, "secret")
```

- [ ] **Step 2: Run `python3 -m unittest tests.test_distribution_search -v`; verify failure for the missing module and fields.**

- [ ] **Step 3: Implement the frozen request dataclass and one validator.**

```python
@dataclass(frozen=True)
class DistributionRequest:
    mode: str = "pure"
    selector: str = "auto"
    max_components: int = 3

    def __post_init__(self):
        if self.mode not in {"pure", "combination"}:
            raise ValueError("distribution mode must be pure or combination.")
        if self.selector not in {"auto", "centered_binomial", "sparse_ternary"}:
            raise ValueError("distribution selector is invalid.")
        if not isinstance(self.max_components, int) or not 1 <= self.max_components <= 6:
            raise ValueError("max_distribution_components must be an integer between 1 and 6.")
```

Use existing CBD and sparse-ternary parameter tables as base components. Use `itertools.combinations_with_replacement` for unordered sums. Pure mode yields one component; combination mode yields component counts 1 through the configured limit. Composite metadata sums means, variances, and numeric supports and emits `estimator.type == "composite_moment"`.

- [ ] **Step 4: Add mode and limit fields to both request dataclasses.** Parse snake_case and camelCase names, default to pure/3, and reject `errorDistributionMode=combination` for LWR-family variants. Preserve existing selectors.

- [ ] **Step 5: Run `python3 -m unittest tests.test_distribution_search tests.test_parameter_search tests.test_ntru_search -v`; expect PASS. Commit with `git commit -m "feat: add bounded pure and composite distributions"`.**

### Task 2: Unify lazy candidate ordering and adaptive validation

**Files:**
- Create: `app/adaptive_search.py`
- Modify: `app/parameter_search.py` (RLWE candidate iterator and recommendation loop)
- Modify: `app/ntru_search.py` (NTRU candidate iterator and recommendation loop)
- Test: `tests/test_adaptive_search.py`, `tests/test_parameter_search.py`, `tests/test_ntru_search.py`

**Interfaces:**
- `AdaptiveValidationResult(attempted, successful, validated, target_met, exhausted, status, best_candidate)`.
- `adaptive_validate(candidates, *, estimate, normalize, apply, meets_target, order_key, on_progress=None, cancel=None)`.

- [ ] **Step 1: Write a test with 90, 127, and 128-bit fake outcomes; assert all three are attempted and the first lexical target hit is returned.**
- [ ] **Step 2: Write a test with only a 90-bit outcome; assert `target_unmet` and `no_feasible_candidate` after exhaustion.**
- [ ] **Step 3: Run `python3 -m unittest tests.test_adaptive_search -v`; verify the new helper is missing.**
- [ ] **Step 4: Implement the helper so it consumes candidates in `(n, q, distribution_order_key)` order, continues after failure/cancellation, and stops only when the selected measured result meets target.**
- [ ] **Step 5: Remove RLWE's default one-candidate stop and NTRU's fixed successful-count stop. Do not call `select_best_distribution_per_modulus` before estimator validation; preserve every distribution candidate. Return the best checked item only as a reference when exhausted.**
- [ ] **Step 6: Add pure NTRU and RLWE fixtures with target 128, MATZOV classical, `x^n+1`, q bits 7–12, centered-binomial Secret/Error, and NTT `n/2 | q-1`; assert NTRU routes to standard and RLWE to enhanced.**
- [ ] **Step 7: Run `python3 -m unittest tests.test_adaptive_search tests.test_parameter_search tests.test_ntru_search -v`; expect PASS. Commit with `git commit -m "fix: unify adaptive minimum-parameter search"`.**

### Task 3: Make estimator work attack-observable and cancellable

**Files:**
- Create: `app/estimator_tasks.py`
- Modify: `app/estimator_runner.py` (task routing and composite mapping)
- Modify: `app/estimator_process.py` (cancellable Sage process)
- Modify: `app/job_progress.py` (candidate/model/mode/attack fields)
- Test: `tests/test_estimator_tasks.py`, `tests/test_estimator_runner.py`, `tests/test_job_progress.py`

**Interfaces:**
- `EstimatorTask(model: str, mode: str, attack: str)`.
- `run_estimator_task(payload, task, config, profile, cancel_event) -> dict[str, Any]`.
- Progress events include `candidate`, `model`, `mode`, `attack`, `completed`, `total`, and `cancelled`.

- [ ] **Step 1: Add failing tests for composite mapping and a pre-set cancellation event.**
- [ ] **Step 2: Run `python3 -m unittest tests.test_estimator_tasks tests.test_estimator_runner tests.test_job_progress -v`; verify failure.**
- [ ] **Step 3: Add optional `model`, `mode`, and `attack` task fields to the Sage runner; deny all other attacks for a task while keeping the existing full four-mode compatibility path.**
- [ ] **Step 4: Replace opaque local `subprocess.run` with a polled `subprocess.Popen` child. Check `cancel_event`, terminate the process group on cancellation, return `attack_cancelled`, and keep local `timeout=None`; keep remote timeouts unchanged.**
- [ ] **Step 5: Extend `ProgressEvent` with optional task fields, preserving existing positional constructors.**
- [ ] **Step 6: Run `python3 -m unittest tests.test_estimator_tasks tests.test_estimator_runner tests.test_job_progress -v`; expect PASS. Commit with `git commit -m "feat: expose cancellable estimator attacks"`.**

### Task 4: Add job cancellation and detailed server status

**Files:**
- Modify: `app/server.py` (job state, progress callback, JSON, HTTP routes)
- Modify: `app/job_progress.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Add `POST /api/agent/jobs/<job_id>/cancel`.
- Add JSON keys `current_candidate`, `current_model`, `current_mode`, `current_attack`, `completed_attacks`, `total_attacks`, and `cancel_requested`.

- [ ] **Step 1: Add failing tests for active-job cancellation, unknown IDs, terminal jobs, and task progress serialization.**
- [ ] **Step 2: Run `python3 -m unittest tests.test_server -v`; verify failure.**
- [ ] **Step 3: Add a per-job `threading.Event`, store the submitted future, update synchronized task fields from `ProgressEvent`, and pass the event through the recommendation call.**
- [ ] **Step 4: Implement the cancel route: 404 unknown, 409 terminal, and 202 `cancellation_requested` for active jobs. Set terminal `cancelled` only after the child process stops and completed results are collected.**
- [ ] **Step 5: Run `python3 -m unittest tests.test_server tests.test_job_progress -v`; expect PASS. Commit with `git commit -m "feat: add estimator job cancellation status"`.**

### Task 5: Update browser controls, labels, and result states

**Files:**
- Modify: `static/index.html` (Secret/Error modules and component limit)
- Modify: `static/app.js` (payload, LWR disabling, translations, progress, cancel, statuses)
- Modify: `static/app-model.js` (result presentation)
- Modify: `static/styles.css`
- Test: `tests/js/app-model.test.cjs`, `tests/test_browser_state.py`

- [ ] **Step 1: Add failing tests asserting pure defaults, component limit 3, LWR Error disabling, and `no_feasible_candidate` presentation.**
- [ ] **Step 2: Run `node --test tests/js/app-model.test.cjs && python3 -m unittest tests.test_browser_state -v`; verify failure.**
- [ ] **Step 3: Add separate Secret/Error mode controls, a global numeric limit with min 1/max 6/value 3, and payload fields `secretDistributionMode`, `errorDistributionMode`, and `maxDistributionComponents`.**
- [ ] **Step 4: Hide/disable Error distribution controls for LWR-family variants and restore them for RLWE/NTRU. Add English/Chinese model labels and explicit no-feasible/cancelled statuses.**
- [ ] **Step 5: Render server task progress and send the cancel request while preserving two-second job polling.**
- [ ] **Step 6: Run `node --test tests/js/app-model.test.cjs && python3 -m unittest tests.test_browser_state -v`; expect PASS. Commit with `git commit -m "feat: expose distribution modes and search status"`.**

### Task 6: Add live fixtures, documentation, and final verification

**Files:**
- Create: `tests/test_live_estimator_search.py`
- Modify: `tests/test_parameter_search.py`, `tests/test_ntru_search.py`, `tests/test_estimator_runner.py`, `tests/test_server.py`
- Modify: `README.md`, `README.zh.md`

- [ ] **Step 1: Add shared pure-mode fixture payloads for NTRU/ring and RLWE using target 128, MATZOV classical, q bits 7–12, centered-binomial Secret/Error, and NTT `n/2 | q-1`; set RLWE `minN=maxN=512` for the same-dimension comparison.**
- [ ] **Step 2: Add mocked-estimator assertions for routing, standard/enhanced profiles, pure metadata, four result groups, adaptive continuation, and target/no-feasible statuses.**
- [ ] **Step 3: Add an opt-in Sage smoke test gated by `EASYLATTICE_RUN_SAGE_TESTS=1` and profile availability; assert JSON shape/status rather than version-specific bit values.**
- [ ] **Step 4: Document pure defaults, the 1–6 limit, LWR Error behavior, model explanations, adaptive search, cancellation, and the smoke command in both README files.**
- [ ] **Step 5: Run `python3 -m unittest discover -s tests -v`, `node --test tests/js/app-model.test.cjs`, and `git diff --check`; expect all tests to pass and no diff-check output.**
- [ ] **Step 6: Run the optional smoke test on a configured Sage machine with `EASYLATTICE_RUN_SAGE_TESTS=1 python3 -m unittest tests.test_live_estimator_search -v`, then commit with `git commit -m "test: cover pure NTRU and RLWE adaptive search"`.**

## Plan self-review

- Spec coverage: request/defaults (Task 1), adaptive lexical search and no-feasible states (Task 2), composite estimator and cancellation (Task 3), server API/progress (Task 4), UI and model labels (Task 5), fixture tests and documentation (Task 6).
- Placeholder scan: no `TBD`, `TODO`, or unspecified implementation tasks remain.
- Type consistency: `DistributionRequest`, `AdaptiveValidationResult`, `EstimatorTask`, and progress keys are defined before later tasks consume them.
- Compatibility: missing mode fields remain pure; existing selectors and result fields remain accepted; remote limits remain bounded.
