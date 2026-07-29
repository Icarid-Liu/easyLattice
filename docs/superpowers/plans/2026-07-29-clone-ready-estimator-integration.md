# Clone-Ready Estimator Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the browser-managed estimator workflow merge-ready for `main`, with non-destructive clone/setup migration, truthful profile status, and fail-closed estimator configuration checks on every recommendation API.

**Architecture:** Keep `app.local_profile` as the sole authority for profile routing and Sage readiness. Add a focused setup-configuration module for atomic create/migrate behavior, have the shell wrapper supply only detected paths, remove ambient-import readiness from public configuration, and route all estimator-enabled APIs and browser status through the explicit Standard/Enhanced profile contract.

**Tech Stack:** Python 3.10 standard library, Bash, Sage subprocesses, vanilla HTML/CSS/JavaScript, Python `unittest`, Node.js built-in test runner, Chromium CDP browser tests.

## Global Constraints

- Standard uses `estimator.lattice_estimator_path` for LWE, LWR, and NTRU.
- Enhanced uses `estimator.enhanced_lattice_estimator_path` for RLWE, MLWE, RLWR, and MLWR.
- A local profile is ready only after its explicit path passes an isolated Sage import-origin preflight.
- Ambient `PYTHONPATH` or an installed `estimator` distribution must never be reported as local profile readiness.
- `./start.sh --with-estimator` may clone only the two known estimator repositories into `.external/`.
- Existing non-empty profile paths and all unrelated configuration fields must be preserved unless the user passes `--force`.
- Paths containing spaces must be passed as argument-array or environment values and never interpolated into shell source.
- `POST /api/agent/jobs`, `POST /api/agent/recommend`, and `POST /api/rlwe/recommend` must return HTTP 409 before search when `useEstimator=true` and the required local profile is unavailable.
- The top-level preflight error remains `estimator_profile_not_configured` and includes `required_profile` plus `profile_error_code`.
- A failure after a real estimator attempt retains the existing explicit `failed`/`partial` validation fallback contract.
- A configured remote estimator worker bypasses local Sage and profile checks.
- LLM remains disabled by default.
- Pushing or merging to `main` is not part of these implementation tasks.

## File Map

- Create `app/setup_config.py`: default config construction, non-destructive profile-path supplementation, invalid preserved-path reporting, atomic writes, and an environment-backed CLI used by setup.
- Create `tests/test_setup_config.py`: focused migration, force, rollback, and path-with-spaces tests.
- Modify `scripts/setup-local.sh`: detect/clone sources, honor `SAGE_BINARY`, and always delegate create/migrate behavior to `app.setup_config`.
- Modify `tests/test_start_script.py`: exercise clone-ready setup in an isolated checkout with local fixture repositories.
- Modify `app/config.py`: stop ambient estimator discovery from influencing public configuration and expose only explicit profile source data.
- Modify `tests/test_agent_config.py`: prove ambient imports are ignored while explicit paths still expose source metadata.
- Modify `app/server.py`: share profile-preflight response handling across asynchronous and synchronous recommendation routes.
- Modify `tests/test_server.py`: assert identical 409 behavior and bypass behavior across all recommendation endpoints.
- Modify `static/app.js`: render truthful local/remote status, include unavailable-profile reasons, and surface `profile_error_code`.
- Modify `tests/test_browser_state.py`: cover absence of `PYTHONPATH/default`, localized profile failures, and Standard/Enhanced route prompts.
- Modify `README.md`, `README.zh.md`, and `docs/architecture.md`: document the clone-ready command, supplementation semantics, macOS Sage path, truthful status, and unified API contract.

---

### Task 1: Atomic Setup Configuration Creation and Migration

**Files:**
- Create: `app/setup_config.py`
- Create: `tests/test_setup_config.py`

**Interfaces:**
- Consumes: `configured_estimator_source_root(path)` and `read_json(path)` from `app.config`.
- Produces: `SetupConfigError`, `SetupConfigUpdate`, `default_setup_config(...)`, `update_setup_config(...)`, and `main()`.

- [ ] **Step 1: Write failing create, supplement, preserve, force, and rollback tests**

Create `tests/test_setup_config.py` with these concrete cases:

```python
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app.setup_config import SetupConfigError, update_setup_config


def estimator_source(root: Path, name: str) -> Path:
    source = root / name
    package = source / "estimator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    return source


class SetupConfigTests(unittest.TestCase):
    def test_creates_defaults_with_paths_containing_spaces(self) -> None:
        with TemporaryDirectory(prefix="easy lattice ") as directory:
            root = Path(directory)
            standard = estimator_source(root, "standard estimator")
            enhanced = estimator_source(root, "enhanced estimator")
            config_path = root / "local config.json"

            result = update_setup_config(
                config_path,
                "/Applications/SageMath 10.7.app/Contents/MacOS/sage",
                str(standard),
                str(enhanced),
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.action, "created")
        self.assertEqual(
            result.supplemented_fields,
            ("lattice_estimator_path", "enhanced_lattice_estimator_path"),
        )
        self.assertEqual(
            saved["estimator"]["sage_binary"],
            "/Applications/SageMath 10.7.app/Contents/MacOS/sage",
        )
        self.assertEqual(saved["estimator"]["lattice_estimator_path"], str(standard.resolve()))
        self.assertEqual(
            saved["estimator"]["enhanced_lattice_estimator_path"],
            str(enhanced.resolve()),
        )
        self.assertFalse(saved["llm"]["enabled"])

    def test_supplements_only_empty_profile_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            standard = estimator_source(root, "detected-standard")
            enhanced = estimator_source(root, "detected-enhanced")
            config_path = root / "config.local.json"
            config_path.write_text(
                json.dumps(
                    {
                        "estimator": {
                            "sage_binary": "/existing/sage",
                            "lattice_estimator_path": "  ",
                            "enhanced_lattice_estimator_path": "/keep/enhanced",
                            "remote_url": "https://worker.example",
                            "remote_timeout_seconds": 99,
                        },
                        "llm": {"enabled": True, "model": "keep-model"},
                        "scripts": {"decrypt_error": ["keep"]},
                        "unrelated": {"keep": True},
                    }
                ),
                encoding="utf-8",
            )

            result = update_setup_config(
                config_path,
                "/new/sage",
                str(standard),
                str(enhanced),
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.action, "updated")
        self.assertEqual(result.supplemented_fields, ("lattice_estimator_path",))
        self.assertEqual(saved["estimator"]["sage_binary"], "/existing/sage")
        self.assertEqual(saved["estimator"]["lattice_estimator_path"], str(standard.resolve()))
        self.assertEqual(saved["estimator"]["enhanced_lattice_estimator_path"], "/keep/enhanced")
        self.assertEqual(saved["estimator"]["remote_url"], "https://worker.example")
        self.assertEqual(saved["estimator"]["remote_timeout_seconds"], 99)
        self.assertEqual(saved["llm"], {"enabled": True, "model": "keep-model"})
        self.assertEqual(saved["scripts"], {"decrypt_error": ["keep"]})
        self.assertEqual(saved["unrelated"], {"keep": True})

    def test_preserves_and_reports_nonempty_invalid_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            detected = estimator_source(root, "detected")
            config_path = root / "config.local.json"
            config_path.write_text(
                json.dumps(
                    {
                        "estimator": {
                            "lattice_estimator_path": "/missing/standard",
                            "enhanced_lattice_estimator_path": None,
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = update_setup_config(
                config_path,
                "sage",
                str(detected),
                str(detected),
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["estimator"]["lattice_estimator_path"], "/missing/standard")
        self.assertEqual(
            saved["estimator"]["enhanced_lattice_estimator_path"],
            str(detected.resolve()),
        )
        self.assertEqual(result.preserved_invalid_fields, ("lattice_estimator_path",))

    def test_force_regenerates_the_complete_default_object(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            standard = estimator_source(root, "standard")
            config_path = root / "config.local.json"
            config_path.write_text('{"unrelated": true}\n', encoding="utf-8")

            result = update_setup_config(
                config_path,
                "/explicit/sage",
                str(standard),
                None,
                force=True,
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.action, "regenerated")
        self.assertNotIn("unrelated", saved)
        self.assertEqual(saved["estimator"]["sage_binary"], "/explicit/sage")
        self.assertIsNone(saved["estimator"]["enhanced_lattice_estimator_path"])

    def test_replace_failure_keeps_original_bytes_and_removes_temp_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            standard = estimator_source(root, "standard")
            config_path = root / "config.local.json"
            original = b'{"estimator":{"lattice_estimator_path":null}}\n'
            config_path.write_bytes(original)

            with mock.patch("app.setup_config.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(SetupConfigError):
                    update_setup_config(config_path, "sage", str(standard), None)

            leftovers = list(root.glob(f".{config_path.name}.*.tmp"))
            self.assertEqual(config_path.read_bytes(), original)

        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test and verify the module is missing**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_setup_config.py' -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.setup_config'`.

- [ ] **Step 3: Implement the setup configuration module**

Create `app/setup_config.py` with these public shapes and behavior:

```python
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .config import configured_estimator_source_root, read_json


PROFILE_PATH_FIELDS = (
    "lattice_estimator_path",
    "enhanced_lattice_estimator_path",
)


class SetupConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SetupConfigUpdate:
    action: Literal["created", "updated", "unchanged", "regenerated"]
    supplemented_fields: tuple[str, ...]
    preserved_invalid_fields: tuple[str, ...]


def _normalized_candidate(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return str(Path(value).expanduser().resolve())


def default_setup_config(
    sage_binary: str,
    standard_path: str | None,
    enhanced_path: str | None,
) -> dict[str, Any]:
    return {
        "estimator": {
            "sage_binary": sage_binary,
            "lattice_estimator_path": _normalized_candidate(standard_path),
            "enhanced_lattice_estimator_path": _normalized_candidate(enhanced_path),
            "default_timeout_seconds": 16,
            "per_attack_timeout_seconds": 12,
            "remote_url": None,
            "remote_timeout_seconds": 240,
            "remote_poll_interval_seconds": 2,
        },
        "llm": {
            "enabled": False,
            "provider": "openai-compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "local-model",
            "api_key_env": "EASYLATTICE_LLM_API_KEY",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer ",
            "timeout_seconds": 30,
        },
        "scripts": {
            "decrypt_error": [],
            "signature_smoothing": [],
        },
    }


def _is_empty(value: object) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def _valid_profile_path(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    root = configured_estimator_source_root(value)
    return bool(root and (root / "estimator" / "__init__.py").is_file())


def _read_existing(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise SetupConfigError("Local configuration must contain a JSON object.")
    estimator = value.get("estimator")
    if estimator is not None and not isinstance(estimator, dict):
        raise SetupConfigError("Local estimator configuration must contain a JSON object.")
    return value


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except Exception as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise SetupConfigError(
            f"Could not update local configuration: {type(exc).__name__}: {exc}"
        ) from exc


def update_setup_config(
    path: Path,
    sage_binary: str,
    standard_path: str | None,
    enhanced_path: str | None,
    *,
    force: bool = False,
) -> SetupConfigUpdate:
    path = path.expanduser()
    existed = path.exists()
    candidates = {
        "lattice_estimator_path": _normalized_candidate(standard_path),
        "enhanced_lattice_estimator_path": _normalized_candidate(enhanced_path),
    }
    if force or not existed:
        value = default_setup_config(sage_binary, standard_path, enhanced_path)
        _atomic_write(path, value)
        supplemented = tuple(field for field in PROFILE_PATH_FIELDS if candidates[field])
        return SetupConfigUpdate(
            action="regenerated" if existed else "created",
            supplemented_fields=supplemented,
            preserved_invalid_fields=(),
        )

    value = _read_existing(path)
    estimator = value.get("estimator")
    if estimator is None:
        estimator = {}
        value["estimator"] = estimator
    supplemented_fields: list[str] = []
    for field in PROFILE_PATH_FIELDS:
        if _is_empty(estimator.get(field)) and candidates[field] is not None:
            estimator[field] = candidates[field]
            supplemented_fields.append(field)

    preserved_invalid_fields = tuple(
        field
        for field in PROFILE_PATH_FIELDS
        if not _is_empty(estimator.get(field))
        and not _valid_profile_path(estimator.get(field))
    )
    if supplemented_fields:
        _atomic_write(path, value)
        action: Literal["updated", "unchanged"] = "updated"
    else:
        action = "unchanged"
    return SetupConfigUpdate(
        action=action,
        supplemented_fields=tuple(supplemented_fields),
        preserved_invalid_fields=preserved_invalid_fields,
    )


def main() -> None:
    path = Path(os.environ["EASYLATTICE_SETUP_CONFIG"])
    result = update_setup_config(
        path,
        os.environ["EASYLATTICE_SETUP_SAGE"],
        os.environ.get("EASYLATTICE_SETUP_ESTIMATOR") or None,
        os.environ.get("EASYLATTICE_SETUP_ENHANCED_ESTIMATOR") or None,
        force=os.environ.get("EASYLATTICE_SETUP_FORCE") == "1",
    )
    print(f"Configuration {result.action}: {path}")
    if result.supplemented_fields:
        print("Supplemented profile fields: " + ", ".join(result.supplemented_fields))
    if result.preserved_invalid_fields:
        print(
            "Preserved non-empty invalid profile fields: "
            + ", ".join(result.preserved_invalid_fields)
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run focused tests and compile the module**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_setup_config.py' -v
python3 -m py_compile app/setup_config.py
git diff --check
```

Expected: all setup-config tests pass and both checks exit zero.

- [ ] **Step 5: Commit the atomic migration unit**

```bash
git add app/setup_config.py tests/test_setup_config.py
git commit -m "Add atomic setup configuration migration"
```

---

### Task 2: Clone-Ready Startup Script Integration

**Files:**
- Modify: `scripts/setup-local.sh:78-181`
- Modify: `tests/test_start_script.py`

**Interfaces:**
- Consumes: `python3 -m app.setup_config` from Task 1.
- Produces: `SAGE_BINARY` setup precedence and optional local-fixture repository URL overrides while retaining `./start.sh`'s existing CLI.

- [ ] **Step 1: Write failing script integration tests**

Add these imports and helpers to `tests/test_start_script.py`:

```python
import shutil


SETUP_SCRIPT = ROOT / "scripts" / "setup-local.sh"


def initialize_estimator_fixture(path: Path) -> None:
    package = path / "estimator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=easyLattice Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=path,
        check=True,
        capture_output=True,
    )


def isolated_checkout(destination: Path) -> Path:
    checkout = destination / "easy lattice checkout"
    shutil.copytree(
        ROOT,
        checkout,
        ignore=shutil.ignore_patterns(
            ".git",
            ".external",
            ".worktrees",
            "__pycache__",
            "*.pyc",
            "config.local.json",
        ),
    )
    return checkout
```

Add these test methods:

```python
    def test_setup_supplements_existing_empty_paths_without_force(self) -> None:
        with tempfile.TemporaryDirectory(prefix="easy lattice setup ") as directory:
            root = Path(directory)
            standard = root / "standard estimator"
            enhanced = root / "enhanced estimator"
            for source in (standard, enhanced):
                package = source / "estimator"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text("", encoding="utf-8")
            config_path = root / "existing config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "estimator": {
                            "sage_binary": "/Applications/Existing Sage.app/sage",
                            "lattice_estimator_path": None,
                            "enhanced_lattice_estimator_path": "",
                            "remote_timeout_seconds": 77,
                        },
                        "unrelated": "keep",
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "EASYLATTICE_CONFIG": str(config_path),
                    "SAGE_BINARY": "/Applications/New Sage.app/sage",
                    "LATTICE_ESTIMATOR_PATH": str(standard),
                    "ENHANCED_LATTICE_ESTIMATOR_PATH": str(enhanced),
                }
            )

            result = subprocess.run(
                [str(SETUP_SCRIPT)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            saved["estimator"]["sage_binary"],
            "/Applications/Existing Sage.app/sage",
        )
        self.assertEqual(saved["estimator"]["lattice_estimator_path"], str(standard.resolve()))
        self.assertEqual(
            saved["estimator"]["enhanced_lattice_estimator_path"],
            str(enhanced.resolve()),
        )
        self.assertEqual(saved["estimator"]["remote_timeout_seconds"], 77)
        self.assertEqual(saved["unrelated"], "keep")

    def test_with_estimator_clones_local_fixtures_in_checkout_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="easy lattice clone ") as directory:
            root = Path(directory)
            checkout = isolated_checkout(root)
            standard_fixture = root / "standard fixture"
            enhanced_fixture = root / "enhanced fixture"
            initialize_estimator_fixture(standard_fixture)
            initialize_estimator_fixture(enhanced_fixture)
            config_path = checkout / "config.local.json"
            sage_path = "/Applications/SageMath 10.7.app/Contents/MacOS/sage"
            environment = os.environ.copy()
            environment.update(
                {
                    "EASYLATTICE_CONFIG": str(config_path),
                    "SAGE_BINARY": sage_path,
                    "EASYLATTICE_STANDARD_ESTIMATOR_REPOSITORY": standard_fixture.as_uri(),
                    "EASYLATTICE_ENHANCED_ESTIMATOR_REPOSITORY": enhanced_fixture.as_uri(),
                }
            )

            result = subprocess.run(
                [str(checkout / "scripts" / "setup-local.sh"), "--with-estimator"],
                cwd=checkout,
                env=environment,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(saved["estimator"]["sage_binary"], sage_path)
        self.assertTrue(
            saved["estimator"]["lattice_estimator_path"].endswith(
                ".external/lattice-estimator"
            )
        )
        self.assertTrue(
            saved["estimator"]["enhanced_lattice_estimator_path"].endswith(
                ".external/enhanced-lattice-estimator"
            )
        )
```

- [ ] **Step 2: Run the script tests and verify the existing-config case fails**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_start_script.py' -v
```

Expected: the existing configuration remains unsupplemented, and the local
fixture clone override is ignored.

- [ ] **Step 3: Replace inline config writing with the migration CLI and add safe overrides**

In `scripts/setup-local.sh`, replace Sage detection with:

```bash
SAGE_BIN="${SAGE_BINARY:-sage}"
if [[ "$SAGE_BIN" == "sage" ]] && command -v sage >/dev/null 2>&1; then
  SAGE_BIN="$(command -v sage)"
fi
```

Define the fixed default repository URLs with testable environment overrides.
Keep each expansion on one shell word:

```bash
STANDARD_ESTIMATOR_REPOSITORY="${EASYLATTICE_STANDARD_ESTIMATOR_REPOSITORY:-https://github.com/malb/lattice-estimator.git}"
ENHANCED_ESTIMATOR_REPOSITORY="${EASYLATTICE_ENHANCED_ESTIMATOR_REPOSITORY:-https://github.com/identitymapping/enhanced_lattice-estimator.git}"
```

Change the two clone commands to:

```bash
git clone --depth=1 "$STANDARD_ESTIMATOR_REPOSITORY" "$ROOT_DIR/.external/lattice-estimator"
git clone --depth=1 "$ENHANCED_ESTIMATOR_REPOSITORY" "$ROOT_DIR/.external/enhanced-lattice-estimator"
```

Delete the `if existing/else inline Python` block at lines 137-182 and always
invoke the Task 1 CLI:

```bash
EASYLATTICE_SETUP_CONFIG="$CONFIG_PATH" \
EASYLATTICE_SETUP_SAGE="$SAGE_BIN" \
EASYLATTICE_SETUP_ESTIMATOR="$ESTIMATOR_PATH" \
EASYLATTICE_SETUP_ENHANCED_ESTIMATOR="$ENHANCED_ESTIMATOR_PATH" \
EASYLATTICE_SETUP_FORCE="$FORCE_CONFIG" \
"$PYTHON_BIN" -m app.setup_config
```

Do not use `eval`, `bash -c`, or a generated shell command for any path.

- [ ] **Step 4: Run startup, shell syntax, and path-with-spaces tests**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_setup_config.py' -v
python3 -m unittest discover -s tests -p 'test_start_script.py' -v
bash -n start.sh scripts/setup-local.sh
git diff --check
```

Expected: all tests pass and both shell/check commands exit zero.

- [ ] **Step 5: Commit the startup integration**

```bash
git add scripts/setup-local.sh tests/test_start_script.py
git commit -m "Make estimator setup supplement existing profiles"
```

---

### Task 3: Remove Ambient Estimator Readiness from Public Configuration

**Files:**
- Modify: `app/config.py:1-10,193-260,343-347`
- Modify: `tests/test_agent_config.py:90-199`

**Interfaces:**
- Consumes: explicit `EstimatorConfig.lattice_estimator_path` and `enhanced_lattice_estimator_path`.
- Produces: public profile fields `configured`, `source_present`, `path`, and `revision`; `version` remains an explicit Standard source revision only.

- [ ] **Step 1: Write a failing ambient-import regression test**

Add this test to `tests/test_agent_config.py`:

```python
    def test_public_config_ignores_ambient_estimator_source(self):
        with TemporaryDirectory() as directory:
            ambient = Path(directory)
            package = ambient / "estimator"
            package.mkdir()
            (package / "__init__.py").write_text(
                '__version__ = "ambient-should-not-appear"\n',
                encoding="utf-8",
            )
            with patch("app.config.estimator_source_root", return_value=ambient):
                data = public_config(AppConfig(estimator=EstimatorConfig()))

        self.assertIsNone(data["estimator"]["version"])
        self.assertEqual(
            data["estimator"]["profiles"]["standard"],
            {
                "configured": False,
                "source_present": False,
                "path": None,
                "revision": None,
            },
        )
```

Update the explicit-profile assertions in
`test_estimator_profiles_report_static_versions_and_availability` to:

```python
        self.assertTrue(data["estimator"]["profiles"]["standard"]["configured"])
        self.assertTrue(data["estimator"]["profiles"]["standard"]["source_present"])
        self.assertTrue(data["estimator"]["profiles"]["enhanced"]["configured"])
        self.assertTrue(data["estimator"]["profiles"]["enhanced"]["source_present"])
```

and remove assertions for the old public `available` field.

- [ ] **Step 2: Run the regression test and verify ambient metadata leaks**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_agent_config.py' -v
```

Expected: FAIL because `version` becomes `ambient-should-not-appear` and the
Standard profile is reported from the patched ambient root.

- [ ] **Step 3: Make public metadata explicit-path-only**

Replace the relevant functions in `app/config.py` with:

```python
def estimator_version(estimator: EstimatorConfig) -> str | None:
    root = configured_estimator_source_root(estimator.lattice_estimator_path)
    if root and (root / "estimator" / "__init__.py").is_file():
        return read_git_version(root) or read_static_estimator_version(root)
    return None


def estimator_profile_data(estimator: EstimatorConfig, profile: str) -> dict[str, Any]:
    if profile == "standard":
        configured_path = estimator.lattice_estimator_path
    elif profile == "enhanced":
        configured_path = estimator.enhanced_lattice_estimator_path
    else:
        raise ValueError("estimator profile must be standard or enhanced.")

    root = configured_estimator_source_root(configured_path)
    source_present = bool(root and (root / "estimator" / "__init__.py").is_file())
    revision = (
        read_git_version(root) or read_static_estimator_version(root)
        if source_present and root is not None
        else None
    )
    return {
        "configured": bool(configured_path),
        "source_present": source_present,
        "path": str(root) if root else None,
        "revision": revision,
    }


def estimator_source_root(estimator: EstimatorConfig) -> Path | None:
    return configured_estimator_source_root(estimator.lattice_estimator_path)
```

Remove `importlib.metadata`, `importlib.util`, and
`read_installed_estimator_version()`. Keep `estimator_source_root()` as a
compatibility helper, but make it explicit-path-only as shown.

- [ ] **Step 4: Run public config and estimator process regressions**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_agent_config.py' -v
python3 -m unittest discover -s tests -p 'test_local_profile.py' -v
python3 -m py_compile app/config.py
git diff --check
```

Expected: all tests pass and ambient discovery no longer affects public data.

- [ ] **Step 5: Commit the explicit public metadata boundary**

```bash
git add app/config.py tests/test_agent_config.py
git commit -m "Report only explicit estimator profile metadata"
```

---

### Task 4: Unify Fail-Closed Preflight Across Recommendation APIs

**Files:**
- Modify: `app/server.py:403-470`
- Modify: `tests/test_server.py:419-517`

**Interfaces:**
- Consumes: `require_available_profile(payload)` and `LocalProfileError`.
- Produces: identical profile preflight HTTP responses for jobs, agent recommendations, and the compatibility recommendation endpoint.

- [ ] **Step 1: Write failing table-driven endpoint tests**

Add this test to `tests/test_server.py`:

```python
    def test_all_recommendation_routes_fail_closed_on_missing_profiles(self):
        self.clear_jobs()
        cases = (
            (
                "/api/agent/jobs",
                {"problem": "ntru", "useEstimator": True},
                "standard",
            ),
            (
                "/api/agent/recommend",
                {
                    "problem": "rlwe",
                    "hardProblemCategory": "lwe",
                    "hardProblemVariant": "rlwe",
                    "useEstimator": True,
                },
                "enhanced",
            ),
            (
                "/api/rlwe/recommend",
                {
                    "problem": "rlwe",
                    "hardProblemCategory": "lwe",
                    "hardProblemVariant": "lwe",
                    "useEstimator": True,
                },
                "standard",
            ),
        )
        try:
            with self.running_server() as server:
                with mock.patch(
                    "app.local_profile.load_config",
                    return_value=AppConfig(estimator=EstimatorConfig()),
                ), mock.patch("app.server.recommend_with_agent") as recommend:
                    for path, request, expected_profile in cases:
                        with self.subTest(path=path):
                            response, payload = self.request_json(
                                server,
                                "POST",
                                path,
                                request,
                                {"Content-Type": "application/json"},
                            )
                            self.assertEqual(response.status, 409)
                            self.assertEqual(
                                payload["code"],
                                "estimator_profile_not_configured",
                            )
                            self.assertEqual(payload["required_profile"], expected_profile)
                            self.assertEqual(
                                payload["profile_error_code"],
                                "estimator_profile_not_configured",
                            )
                recommend.assert_not_called()
                with server_module.jobs_lock:
                    self.assertEqual(server_module.jobs, {})
        finally:
            self.clear_jobs()

    def test_synchronous_recommendation_preflight_bypasses_disabled_and_remote(self):
        cases = (
            (
                {"problem": "rlwe", "useEstimator": False},
                AppConfig(estimator=EstimatorConfig()),
            ),
            (
                {
                    "problem": "rlwe",
                    "hardProblemCategory": "lwe",
                    "hardProblemVariant": "rlwe",
                    "useEstimator": True,
                },
                AppConfig(
                    estimator=EstimatorConfig(remote_url="https://worker.example")
                ),
            ),
        )
        with self.running_server() as server:
            for request, config in cases:
                with self.subTest(request=request), mock.patch(
                    "app.local_profile.load_config",
                    return_value=config,
                ), mock.patch(
                    "app.server.recommend_with_agent",
                    return_value={"ok": True},
                ) as recommend:
                    response, payload = self.request_json(
                        server,
                        "POST",
                        "/api/agent/recommend",
                        request,
                        {"Content-Type": "application/json"},
                    )
                    self.assertEqual(response.status, 200)
                    self.assertEqual(payload, {"ok": True})
                    recommend.assert_called_once_with(request)
```

- [ ] **Step 2: Run server tests and verify synchronous routes still return 200**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_server.py' -v
```

Expected: the job endpoint returns 409, but the two synchronous endpoints call
`recommend_with_agent` and fail the new assertions.

- [ ] **Step 3: Centralize profile preflight response handling**

Add this handler method to `EasyLatticeHandler`:

```python
    def ensure_recommendation_profile(self, payload: dict[str, Any]) -> bool:
        try:
            require_available_profile(payload)
        except LocalProfileError as exc:
            self.write_json(
                exc.as_api_payload(),
                local_profile_error_status(exc),
            )
            return False
        except Exception:
            self.write_json(
                {
                    "ok": False,
                    "code": "config_read_failed",
                    "error": "Could not verify the local estimator configuration.",
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return False
        return True
```

Replace the job endpoint's duplicated `try/except` block with:

```python
            if not self.ensure_recommendation_profile(payload):
                return
```

In the synchronous recommendation block, preflight immediately after JSON
parsing and before `recommend_with_agent`:

```python
        try:
            payload = self.read_json()
            if not self.ensure_recommendation_profile(payload):
                return
            result = recommend_with_agent(payload)
```

Do not change DFR routing or preview/static behavior.

- [ ] **Step 4: Run server and search regressions**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_server.py' -v
python3 -m unittest discover -s tests -p 'test_parameter_search.py' -v
python3 -m unittest discover -s tests -p 'test_ntru_search.py' -v
python3 -m py_compile app/server.py
git diff --check
```

Expected: all tests pass; configuration failures are rejected before search,
while genuine estimator execution failures retain their search-layer result
contract.

- [ ] **Step 5: Commit the unified API boundary**

```bash
git add app/server.py tests/test_server.py
git commit -m "Fail closed across estimator recommendation APIs"
```

---

### Task 5: Render Truthful Browser Status and Specific Profile Errors

**Files:**
- Modify: `static/app.js:142-152,303-335,542-574,798-808,2077-2135,2210-2228`
- Modify: `tests/test_browser_state.py:120-190,959-1110`

**Interfaces:**
- Consumes: public `remote_configured` data and guarded profile records with `available`, `error_code`, and `message`.
- Produces: no local `PYTHONPATH/default` status, profile-specific unavailable reasons, and `profile_error_code` propagation from API errors.

- [ ] **Step 1: Write failing browser status and error-cause assertions**

In the browser fetch fixture, extend `apiError` responses by retaining the
existing arbitrary response object. Add this browser test:

```python
    def test_local_estimator_status_never_uses_pythonpath_default(self):
        self.set_viewport(1440, 1000, mobile=False)
        self.navigate("?missing-profile=1")
        self.page.wait_for(
            "document.readyState === 'complete'"
            " && document.querySelector('#estimator-profile-dialog')?.open"
        )
        self.page.evaluate(
            """(() => {
              window.__profileResponse.profiles.standard.error_code = 'sage_not_found';
              window.__profileResponse.profiles.standard.message = 'Sage was not found.';
              renderEstimatorProfile(window.__profileResponse);
            })()"""
        )

        english = self.page.evaluate(
            """(() => ({
              estimator: document.querySelector('#config-estimator').textContent,
              standard: document.querySelector('#standard-profile-summary').textContent,
              body: document.body.innerText,
            }))()"""
        )
        self.assertEqual(english["estimator"], "estimator: local profiles")
        self.assertIn("Sage executable was not found", english["standard"])
        self.assertNotIn("PYTHONPATH/default", english["body"])

        self.page.evaluate(
            """(() => {
              const language = document.querySelector('#language-select');
              language.value = 'zh';
              language.dispatchEvent(new Event('change', { bubbles: true }));
            })()"""
        )
        chinese = self.page.evaluate(
            "document.querySelector('#standard-profile-summary').textContent"
        )
        self.assertIn("找不到 Sage 可执行文件", chinese)

    def test_api_profile_error_cause_is_shown_in_required_profile_dialog(self):
        self.set_viewport(1440, 1000, mobile=False)
        self.navigate("")
        self.page.wait_for("document.readyState === 'complete' && window.__requests.length === 1")
        self.page.evaluate(
            """window.__requests[0].resolveResult({
              recommendation: {},
              request: { target_security: 128 },
              validation: { status: 'not_requested' },
              alternatives: [],
              search: {},
            })"""
        )
        self.page.wait_for("!searchState.snapshot().inFlight")
        self.page.evaluate(
            """(() => {
              document.querySelector('#use-estimator').checked = true;
              document.querySelector('#parameter-form').requestSubmit();
            })()"""
        )
        self.page.wait_for("window.__requests.length === 2")
        self.page.evaluate(
            """window.__requests[1].resolveResult({
              ok: false,
              code: 'estimator_profile_not_configured',
              error: 'The enhanced estimator profile is not available.',
              required_profile: 'enhanced',
              profile_error_code: 'estimator_origin_mismatch',
            }, 409)"""
        )
        self.page.wait_for("document.querySelector('#estimator-profile-dialog')?.open")
        message = self.page.evaluate(
            "document.querySelector('#estimator-profile-message').textContent"
        )
        self.assertEqual(message, "Sage imported estimator from a different path.")
```

- [ ] **Step 2: Run the browser test and verify the old generic display**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_browser_state.py' -v
```

Expected: FAIL because the header still contains `PYTHONPATH/default`, missing
profiles omit the cause, and API errors discard `profile_error_code`.

- [ ] **Step 3: Add exact bilingual status strings and render profile causes**

Add these English translations:

```javascript
configEstimatorLocalProfiles: "estimator: local profiles",
standardProfileUnavailable: "Standard: unavailable · {reason}",
enhancedProfileUnavailable: "Enhanced: unavailable · {reason}",
```

Add these Chinese translations:

```javascript
configEstimatorLocalProfiles: "estimator：本地 profiles",
standardProfileUnavailable: "Standard：不可用 · {reason}",
enhancedProfileUnavailable: "Enhanced：不可用 · {reason}",
```

Replace the local portion of `renderPublicConfig` with:

```javascript
  if (config.estimator.remote_configured) {
    document.querySelector("#config-estimator").textContent = t("configEstimator", {
      parts: [
        "remote",
        `timeout ${config.estimator.remote_timeout_seconds}s`,
        config.estimator.remote_url,
      ].join(" · "),
    });
  } else {
    document.querySelector("#config-estimator").textContent =
      t("configEstimatorLocalProfiles");
  }
```

Replace `profileSummary` with:

```javascript
function profileSummary(profileName, profile) {
  if (!profile?.available) {
    const errorKey = PROFILE_ERROR_KEYS[profile?.error_code];
    const reason = errorKey
      ? t(errorKey)
      : profile?.message || t("profileErrorNotConfigured");
    return t(
      profileName === "standard"
        ? "standardProfileUnavailable"
        : "enhancedProfileUnavailable",
      { reason },
    );
  }
  const commit = profile.commit || t("notAvailable");
  return t(profileName === "standard" ? "standardProfileReady" : "enhancedProfileReady", {
    path: profile.path || t("notAvailable"),
    commit,
    dirty: profile.dirty ? t("dirtyWorktree") : "",
  });
}
```

- [ ] **Step 4: Preserve and surface `profile_error_code`**

Extend `apiError`:

```javascript
function apiError(result, fallbackKey) {
  const error = errorWithFallback(result?.error, fallbackKey);
  error.code = result?.code || null;
  error.requiredProfile = result?.required_profile || null;
  error.profileErrorCode = result?.profile_error_code || null;
  return error;
}
```

Pass it from the estimator request catch:

```javascript
      openEstimatorProfileDialog({
        requiredProfile: error.requiredProfile,
        profileErrorCode: error.profileErrorCode,
      });
```

Resolve the dialog message in `openEstimatorProfileDialog`:

```javascript
  const requiredProfile = options.requiredProfile;
  const profileErrorKey = PROFILE_ERROR_KEYS[options.profileErrorCode];
  estimatorProfileDialogMessage = profileErrorKey
    ? { key: profileErrorKey, type: "error" }
    : requiredProfile
      ? {
          key: requiredProfile === "enhanced"
            ? "enhancedProfileRequired"
            : "standardProfileRequired",
          type: "error",
        }
      : null;
```

When the client blocks before submission, pass the current record's cause:

```javascript
  const record = estimatorProfileState?.profiles?.[required];
  openEstimatorProfileDialog({
    requiredProfile: required,
    profileErrorCode: record?.error_code,
  });
```

- [ ] **Step 5: Run browser, JavaScript, and model checks**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_browser_state.py' -v
node --test tests/js/app-model.test.cjs
node --check static/app.js
git diff --check
```

Expected: all tests pass in English and Chinese, and no live local status
contains `PYTHONPATH/default`.

- [ ] **Step 6: Commit the truthful browser status**

```bash
git add static/app.js tests/test_browser_state.py
git commit -m "Show truthful estimator profile readiness"
```

---

### Task 6: Documentation, Complete Regression Gate, and Merge Evidence

**Files:**
- Modify: `README.md:270-390`
- Modify: `README.zh.md:229-360`
- Modify: `docs/architecture.md`

**Interfaces:**
- Consumes: the behavior delivered by Tasks 1-5.
- Produces: exact clone/setup guidance and recorded release-gate evidence for review.

- [ ] **Step 1: Update English setup and migration guidance**

Replace the existing instruction that tells users to add `--force` whenever a
config exists with:

```markdown
`./start.sh --with-estimator` clones either missing estimator source tree and
creates `config.local.json`. If the file already exists, setup fills only an
absent, `null`, or empty Standard/Enhanced path. Existing non-empty paths,
timeouts, remote-worker settings, LLM settings, scripts, and unrelated fields
are preserved. Use `--force` only when you intentionally want to regenerate
the complete local configuration.
```

Add the macOS invocation:

````markdown
If Sage is not on `PATH`, provide its executable explicitly. Paths containing
spaces are supported:

```bash
SAGE_BINARY="/Applications/SageMath-10-7.app/Contents/Frameworks/Sage.framework/Versions/10.7/local/bin/sage" \
./start.sh --with-estimator
```
````

Document that `PYTHONPATH/default` is not a ready profile and that the local
status is authoritative only after isolated Sage preflight.

- [ ] **Step 2: Add the equivalent Chinese guidance**

Add this exact behavior statement to `README.zh.md`:

```markdown
`./start.sh --with-estimator` 会 clone 缺少的 estimator 源码树。若
`config.local.json` 已存在，setup 只补全缺失、`null` 或空字符串的 Standard/Enhanced
路径；已有非空路径、超时、远程 worker、LLM、scripts 及其他字段都会保留。只有明确要
重新生成完整本地配置时才使用 `--force`。
```

Add:

````markdown
如果 Sage 不在 `PATH` 中，可显式传入可执行文件；包含空格的路径会作为单个值处理：

```bash
SAGE_BINARY="/Applications/SageMath-10-7.app/Contents/Frameworks/Sage.framework/Versions/10.7/local/bin/sage" \
./start.sh --with-estimator
```
````

State that `PYTHONPATH/default` does not mean a profile is available.

- [ ] **Step 3: Update architecture ownership and API contract**

In `docs/architecture.md`, record:

```markdown
- `app.setup_config` owns atomic setup-time creation and non-destructive
  supplementation of local profile paths.
- `app.local_profile` is the only readiness and request-routing authority.
- Public configuration exposes explicit source metadata but does not infer
  readiness from ambient imports.
- All three recommendation POST routes apply the same local profile preflight
  before search or job creation.
```

Include the stable HTTP 409 fields:

```text
code=estimator_profile_not_configured
required_profile=standard|enhanced
profile_error_code=<safe specific cause>
```

- [ ] **Step 4: Run the complete release gate**

Run:

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/app-model.test.cjs
python3 -m py_compile app/*.py deploy/huggingface-estimator/space_app.py
bash -n start.sh scripts/setup-local.sh
node --check static/app-model.js
node --check static/app.js
node --check static/preview-data.js
git diff --check
```

Expected: every non-opt-in test passes; the pinned real-estimator network smoke
may remain skipped under its existing environment guard.

- [ ] **Step 5: Record branch ancestry and diff scope for merge review**

Run:

```bash
git merge-base --is-ancestor main HEAD
git log --oneline main..HEAD
git diff --stat main...HEAD
git status --short
```

Expected:

- the ancestry command exits zero;
- the log contains the browser-profile commits and the five clone-ready code
  implementation commits;
- the diff contains only the approved estimator integration, its tests, and
  documentation;
- `git status --short` lists only `README.md`, `README.zh.md`, and
  `docs/architecture.md` before the final documentation commit.

- [ ] **Step 6: Commit documentation and release evidence**

```bash
git add README.md README.zh.md docs/architecture.md
git commit -m "Document clone-ready estimator setup"
```

- [ ] **Step 7: Re-run final cleanliness checks**

Run:

```bash
git diff --check HEAD^ HEAD
git status --short
git log -6 --oneline
```

Expected: the commit diff is clean, the working tree is empty, and the latest
six commits are the five code implementation units plus this documentation
commit.
