# README Configuration, Testing, and Reproducibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the English and Chinese READMEs accurately document estimator configuration, reproducible example provenance, and the repository's current validation commands.

**Architecture:** Keep runtime behavior unchanged and update only the two language-specific README files. Treat the existing example table as deterministic fast-screen output, then document the two optional estimator profile commits separately so provenance remains accurate.

**Tech Stack:** GitHub-flavored Markdown, Bash command examples, Python `unittest`, Node.js test runner.

## Global Constraints

- Modify only `README.md` and `README.zh.md` during implementation.
- Do not add machine-specific estimator directory conventions.
- Use exactly eight hexadecimal characters for displayed estimator commits: `3e48ef42` and `876b6617`.
- Keep English and Chinese sections structurally aligned.
- Do not claim that fast-screen example values were produced by an estimator.

---

### Task 1: Update English and Chinese README contracts

**Files:**
- Modify: `README.md`
- Modify: `README.zh.md`

**Interfaces:**
- Consumes: estimator settings from `config.local.example.json`, commit pins from both deployment Dockerfiles, and runnable commands from the current test tree.
- Produces: paired English/Chinese configuration, reproducibility, and testing instructions.

- [ ] **Step 1: Mark example-table estimator provenance accurately**

Add an `Estimator commit` / `Estimator commit` column to the representative result tables. Set every existing row to `not used` / `未使用`, because the controls immediately above the table specify estimator validation is off.

After each table, add the profile mapping:

```text
standard (LWE/LWR/NTRU): 3e48ef42
enhanced (RLWE/MLWE/RLWR/MLWR): 876b6617
```

State that these commits apply only when optional estimator validation is enabled.

- [ ] **Step 2: Complete local estimator configuration documentation**

In both configuration sections, document these JSON fields already present in `config.local.example.json`:

```text
sage_binary
lattice_estimator_path
enhanced_lattice_estimator_path
default_timeout_seconds
per_attack_timeout_seconds
remote_url
remote_timeout_seconds
remote_poll_interval_seconds
```

Add a sentence stating that Sage and estimator source trees may live in any directories visible to the same runtime environment as easyLattice. Preserve the existing JSON and environment-variable configuration mechanisms without suggesting a personal directory name.

Extend the local environment example to include the timeout overrides that `app.config.load_config()` accepts:

```bash
EASYLATTICE_ESTIMATOR_TIMEOUT=240 \
EASYLATTICE_ESTIMATOR_PER_ATTACK_TIMEOUT=60 \
SAGE_BINARY=/path/to/sage \
LATTICE_ESTIMATOR_PATH=/path/to/lattice-estimator \
ENHANCED_LATTICE_ESTIMATOR_PATH=/path/to/enhanced-lattice-estimator \
python3 -m app.server
```

- [ ] **Step 3: Replace the minimal test section with current commands**

Document the full default suites:

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/app-model.test.cjs
```

Document syntax and compilation checks:

```bash
python3 -m py_compile app/*.py deploy/huggingface-estimator/space_app.py
bash -n scripts/setup-local.sh
node --check static/app-model.js
node --check static/app.js
node --check static/preview-data.js
```

Document the opt-in pinned enhanced-estimator checkout smoke and state that it requires Git, network access, and extra runtime:

```bash
EASYLATTICE_RUN_PINNED_ESTIMATOR_SMOKE=1 \
python3 -m unittest discover -s tests -p 'test_estimator_runner.py' \
  -k test_pinned_enhanced_estimator_checkout_has_expected_package_origin -v
```

- [ ] **Step 4: Verify all documented commands and Markdown structure**

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

Expected: Python reports 189 passes and one opt-in network smoke skipped; Node reports 13 passes; all syntax, compilation, and diff checks exit zero.

Run a local-link check without modifying repository files:

```bash
python3 - <<'PY'
import re
from pathlib import Path

for name in ("README.md", "README.zh.md"):
    text = Path(name).read_text(encoding="utf-8")
    missing = []
    for target in re.findall(r"\[[^]]*\]\(([^)]+)\)", text):
        local = target.split("#", 1)[0]
        if local and "://" not in local and not Path(local).exists():
            missing.append(target)
    if missing:
        raise SystemExit(f"{name}: missing local links: {missing}")
PY
```

Expected: exit zero with no output.

- [ ] **Step 5: Review language parity and commit**

Run:

```bash
rg '^## ' README.md README.zh.md
rg -n '3e48ef42|876b6617|per_attack_timeout_seconds|EASYLATTICE_RUN_PINNED_ESTIMATOR_SMOKE' README.md README.zh.md
```

Expected: both READMEs contain corresponding section structures and all four reproducibility/configuration markers.

Commit:

```bash
git add README.md README.zh.md
git commit -m "Update README configuration and test guidance"
```
