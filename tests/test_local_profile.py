from __future__ import annotations

import os
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


class LocalProfileParserTests(unittest.TestCase):
    def make_repository(self, root: Path) -> Path:
        package = root / "estimator"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        return package

    def test_parse_normalizes_repository_and_package_paths(self):
        from app.local_profile import parse_profile_request

        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.make_repository(root)

            parsed = parse_profile_request(
                {
                    "sage_binary": ' "sage" ',
                    "lattice_estimator_path": f'"{package}"',
                    "enhanced_lattice_estimator_path": None,
                }
            )

        self.assertEqual(parsed.sage_binary, "sage")
        self.assertEqual(parsed.lattice_estimator_path, str(root.resolve()))
        self.assertIsNone(parsed.enhanced_lattice_estimator_path)

    def test_parse_expands_home_and_accepts_empty_optional_path(self):
        from app.local_profile import parse_profile_request

        with TemporaryDirectory() as directory:
            home = Path(directory)
            standard = home / "standard"
            self.make_repository(standard)

            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                parsed = parse_profile_request(
                    {
                        "sage_binary": " sage ",
                        "lattice_estimator_path": "~/standard",
                        "enhanced_lattice_estimator_path": "  ",
                    }
                )

        self.assertEqual(parsed.lattice_estimator_path, str(standard.resolve()))
        self.assertIsNone(parsed.enhanced_lattice_estimator_path)

    def test_parse_removes_exactly_one_matching_quote_pair(self):
        from app.local_profile import LocalProfileError, parse_profile_request

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)

            parsed = parse_profile_request(
                {
                    "sage_binary": "'sage'",
                    "lattice_estimator_path": f"'{root}'",
                    "enhanced_lattice_estimator_path": None,
                }
            )
            self.assertEqual(parsed.sage_binary, "sage")

            with self.assertRaises(LocalProfileError) as raised:
                parse_profile_request(
                    {
                        "sage_binary": '""sage""',
                        "lattice_estimator_path": str(root),
                        "enhanced_lattice_estimator_path": None,
                    }
                )

        self.assertEqual(raised.exception.code, "invalid_profile_request")

    def test_parse_rejects_unknown_fields_before_normalizing_values(self):
        from app.local_profile import LocalProfileError, parse_profile_request

        with self.assertRaises(LocalProfileError) as raised:
            parse_profile_request(
                {
                    "sage_binary": "sage",
                    "lattice_estimator_path": "/definitely/missing",
                    "enhanced_lattice_estimator_path": None,
                    "command": "rm -rf /",
                }
            )

        self.assertEqual(raised.exception.code, "invalid_profile_request")
        self.assertIn("unknown_fields", raised.exception.details)

        with self.assertRaises(LocalProfileError) as raised:
            parse_profile_request({7: "not a JSON object key"})
        self.assertEqual(raised.exception.code, "invalid_profile_request")

    def test_parse_rejects_non_object_and_missing_or_empty_required_fields(self):
        from app.local_profile import LocalProfileError, parse_profile_request

        invalid_payloads = (
            None,
            [],
            {},
            {
                "sage_binary": "sage",
                "lattice_estimator_path": "",
                "enhanced_lattice_estimator_path": None,
            },
            {
                "sage_binary": "",
                "lattice_estimator_path": "/tmp/standard",
                "enhanced_lattice_estimator_path": None,
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(LocalProfileError) as raised:
                    parse_profile_request(payload)
                self.assertEqual(raised.exception.code, "invalid_profile_request")

    def test_parse_rejects_non_string_values_nul_and_oversized_values(self):
        from app.local_profile import LocalProfileError, parse_profile_request

        cases = (
            ("sage_binary", 3),
            ("lattice_estimator_path", ["/tmp/standard"]),
            ("enhanced_lattice_estimator_path", 7),
            ("sage_binary", "sage\0--version"),
            ("lattice_estimator_path", "/tmp/standard\0/estimator"),
            ("enhanced_lattice_estimator_path", "x" * 4097),
        )
        for field, value in cases:
            payload = {
                "sage_binary": "sage",
                "lattice_estimator_path": "/tmp/standard",
                "enhanced_lattice_estimator_path": None,
            }
            payload[field] = value
            with self.subTest(field=field, value_type=type(value).__name__):
                with self.assertRaises(LocalProfileError) as raised:
                    parse_profile_request(payload)
                self.assertEqual(raised.exception.code, "invalid_profile_request")

    def test_parse_rejects_paths_without_estimator_package(self):
        from app.local_profile import LocalProfileError, parse_profile_request

        with TemporaryDirectory() as directory:
            with self.assertRaises(LocalProfileError) as raised:
                parse_profile_request(
                    {
                        "sage_binary": "sage",
                        "lattice_estimator_path": directory,
                        "enhanced_lattice_estimator_path": None,
                    }
                )

        self.assertEqual(raised.exception.code, "estimator_path_invalid")


class LocalProfileRuntimeTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        package = root / "estimator"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")

    def test_prepare_runtime_isolates_standard_and_enhanced_profiles(self):
        from app.config import ROOT, EstimatorConfig
        from app.local_profile import prepare_estimator_runtime

        with TemporaryDirectory() as directory:
            parent = Path(directory)
            standard = parent / "standard"
            enhanced = parent / "enhanced"
            self.make_repository(standard)
            self.make_repository(enhanced)
            estimator = EstimatorConfig(
                sage_binary="sage",
                lattice_estimator_path=str(standard),
                enhanced_lattice_estimator_path=str(enhanced),
            )

            with mock.patch("app.local_profile.shutil.which", return_value="/usr/bin/sage"):
                standard_runtime = prepare_estimator_runtime(estimator, "standard")
                enhanced_runtime = prepare_estimator_runtime(estimator, "enhanced")

        self.assertEqual(standard_runtime.sage_binary, "/usr/bin/sage")
        self.assertEqual(standard_runtime.root, standard.resolve())
        self.assertEqual(enhanced_runtime.root, enhanced.resolve())
        for runtime in (standard_runtime, enhanced_runtime):
            self.assertEqual(runtime.environment["PYTHONNOUSERSITE"], "1")
            self.assertEqual(runtime.environment["EASYLATTICE_ESTIMATOR_ROOT"], str(runtime.root))
            self.assertEqual(
                runtime.environment["PYTHONPATH"].split(os.pathsep),
                [str(runtime.root), str(ROOT)],
            )

    def test_prepare_runtime_rejects_missing_non_executable_and_invalid_paths(self):
        from app.config import EstimatorConfig
        from app.local_profile import LocalProfileError, prepare_estimator_runtime

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            missing_sage = EstimatorConfig(
                sage_binary="missing-sage",
                lattice_estimator_path=str(root),
            )
            with mock.patch("app.local_profile.shutil.which", return_value=None):
                with self.assertRaises(LocalProfileError) as raised:
                    prepare_estimator_runtime(missing_sage, "standard")
            self.assertEqual(raised.exception.code, "sage_not_found")

            sage = root / "sage"
            sage.write_text("#!/bin/sh\n", encoding="utf-8")
            sage.chmod(0o644)
            non_executable = EstimatorConfig(
                sage_binary=str(sage),
                lattice_estimator_path=str(root),
            )
            with mock.patch("app.local_profile.shutil.which", return_value=None):
                with self.assertRaises(LocalProfileError) as raised:
                    prepare_estimator_runtime(non_executable, "standard")
            self.assertEqual(raised.exception.code, "sage_not_executable")

            invalid = EstimatorConfig(
                sage_binary="sage",
                lattice_estimator_path=str(root / "missing"),
            )
            with mock.patch("app.local_profile.shutil.which", return_value="/usr/bin/sage"):
                with self.assertRaises(LocalProfileError) as raised:
                    prepare_estimator_runtime(invalid, "standard")
            self.assertEqual(raised.exception.code, "estimator_path_invalid")

    def test_prepare_runtime_accepts_an_explicit_executable(self):
        from app.config import EstimatorConfig
        from app.local_profile import prepare_estimator_runtime

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            sage = root / "sage"
            sage.write_text("#!/bin/sh\n", encoding="utf-8")
            sage.chmod(0o755)
            estimator = EstimatorConfig(
                sage_binary=str(sage),
                lattice_estimator_path=str(root),
            )
            with mock.patch("app.local_profile.shutil.which", return_value=None):
                runtime = prepare_estimator_runtime(estimator, "standard")

        self.assertEqual(runtime.sage_binary, str(sage.resolve()))

    def test_origin_preflight_uses_fixed_command_and_environment(self):
        from app.config import ROOT
        from app.local_profile import (
            ESTIMATOR_ORIGIN_PREFLIGHT,
            EstimatorRuntime,
            run_origin_preflight,
        )

        runtime = EstimatorRuntime(
            sage_binary="/usr/bin/sage",
            root=Path("/tmp/estimator").resolve(),
            environment={"PYTHONPATH": "isolated", "PYTHONNOUSERSITE": "1"},
        )
        completed = subprocess.CompletedProcess([], 0, stdout='{"ok": true}\n', stderr="")
        with mock.patch("app.local_profile.subprocess.run", return_value=completed) as run:
            result = run_origin_preflight(runtime, 9)

        self.assertEqual(result, {"ok": True})
        run.assert_called_once_with(
            [
                "/usr/bin/sage",
                "-python",
                "-c",
                ESTIMATOR_ORIGIN_PREFLIGHT,
                str(runtime.root),
                str(ROOT),
            ],
            text=True,
            capture_output=True,
            timeout=9,
            check=False,
            env=runtime.environment,
            shell=False,
        )

    def test_origin_preflight_has_stable_failure_codes(self):
        from app.local_profile import EstimatorRuntime, LocalProfileError, run_origin_preflight

        runtime = EstimatorRuntime("sage", Path("/tmp/estimator"), {})
        failures = (
            (
                subprocess.CompletedProcess([], 3, stdout="", stderr="import failed\n"),
                "estimator_preflight_failed",
            ),
            (
                subprocess.CompletedProcess([], 0, stdout="not-json\n", stderr=""),
                "estimator_preflight_failed",
            ),
            (
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=(
                        '{"ok": false, "code": "estimator_origin_mismatch", '
                        '"message": "wrong root"}\n'
                    ),
                    stderr="",
                ),
                "estimator_origin_mismatch",
            ),
            (
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout='{"ok": false, "code": "untrusted", "message": "bad"}\n',
                    stderr="",
                ),
                "estimator_preflight_failed",
            ),
        )
        for completed, code in failures:
            with self.subTest(code=code, returncode=completed.returncode):
                with mock.patch("app.local_profile.subprocess.run", return_value=completed):
                    with self.assertRaises(LocalProfileError) as raised:
                        run_origin_preflight(runtime, 5)
                self.assertEqual(raised.exception.code, code)

        with mock.patch(
            "app.local_profile.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["sage"], 5),
        ):
            with self.assertRaises(LocalProfileError) as raised:
                run_origin_preflight(runtime, 5)
        self.assertEqual(raised.exception.code, "estimator_preflight_timeout")

        with mock.patch(
            "app.local_profile.subprocess.run",
            side_effect=OSError("cannot execute"),
        ):
            with self.assertRaises(LocalProfileError) as raised:
                run_origin_preflight(runtime, 5)
        self.assertEqual(raised.exception.code, "estimator_preflight_failed")


class LocalProfileGitTests(unittest.TestCase):
    def test_git_metadata_returns_short_commit_and_dirty_state(self):
        from app.local_profile import git_metadata

        root = Path("/tmp/estimator").resolve()
        commit = subprocess.CompletedProcess([], 0, stdout="0123456789abcdef\n", stderr="")
        status = subprocess.CompletedProcess([], 0, stdout=" M estimator/foo.py\n", stderr="")
        with mock.patch(
            "app.local_profile.subprocess.run",
            side_effect=(commit, status),
        ) as run:
            metadata = git_metadata(root)

        self.assertEqual(metadata.commit, "01234567")
        self.assertIs(metadata.dirty, True)
        self.assertIsNone(metadata.message)
        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    text=True,
                    capture_output=True,
                    timeout=2,
                    check=False,
                ),
                mock.call(
                    ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                    text=True,
                    capture_output=True,
                    timeout=2,
                    check=False,
                ),
            ],
        )

    def test_git_metadata_reports_clean_and_non_git_states(self):
        from app.local_profile import git_metadata

        root = Path("/tmp/estimator").resolve()
        commit = subprocess.CompletedProcess([], 0, stdout="fedcba9876543210\n", stderr="")
        clean = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch("app.local_profile.subprocess.run", side_effect=(commit, clean)):
            metadata = git_metadata(root)
        self.assertEqual(metadata.commit, "fedcba98")
        self.assertIs(metadata.dirty, False)
        self.assertIsNone(metadata.message)

        not_git = subprocess.CompletedProcess([], 128, stdout="", stderr="not a repository\n")
        with mock.patch("app.local_profile.subprocess.run", return_value=not_git):
            metadata = git_metadata(root)
        self.assertIsNone(metadata.commit)
        self.assertIsNone(metadata.dirty)
        self.assertIn("Git metadata unavailable", metadata.message or "")

        with mock.patch("app.local_profile.subprocess.run", side_effect=OSError("git missing")):
            metadata = git_metadata(root)
        self.assertIsNone(metadata.commit)
        self.assertIsNone(metadata.dirty)
        self.assertIn("Git metadata unavailable", metadata.message or "")


class LocalProfileStateTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        package = root / "estimator"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")

    def test_profile_record_has_stable_success_and_absent_shapes(self):
        from app.config import EstimatorConfig
        from app.local_profile import EstimatorRuntime, GitMetadata, profile_record

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            estimator = EstimatorConfig(
                sage_binary="sage",
                lattice_estimator_path=str(root),
            )
            runtime = EstimatorRuntime("/usr/bin/sage", root.resolve(), {})
            with (
                mock.patch("app.local_profile.prepare_estimator_runtime", return_value=runtime),
                mock.patch("app.local_profile.run_origin_preflight", return_value={"ok": True}),
                mock.patch(
                    "app.local_profile.git_metadata",
                    return_value=GitMetadata("01234567", True, None),
                ),
            ):
                standard = profile_record(estimator, "standard")

            enhanced = profile_record(estimator, "enhanced")

        self.assertEqual(
            standard,
            {
                "available": True,
                "path": str(root.resolve()),
                "commit": "01234567",
                "dirty": True,
                "error_code": None,
                "message": None,
            },
        )
        self.assertEqual(
            enhanced,
            {
                "available": False,
                "path": None,
                "commit": None,
                "dirty": None,
                "error_code": "estimator_profile_not_configured",
                "message": "enhanced estimator path is not configured.",
            },
        )

    def test_profile_record_exposes_preflight_errors_without_raising(self):
        from app.config import EstimatorConfig
        from app.local_profile import EstimatorRuntime, LocalProfileError, profile_record

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            estimator = EstimatorConfig(lattice_estimator_path=str(root))
            runtime = EstimatorRuntime("sage", root.resolve(), {})
            with (
                mock.patch("app.local_profile.prepare_estimator_runtime", return_value=runtime),
                mock.patch(
                    "app.local_profile.run_origin_preflight",
                    side_effect=LocalProfileError("estimator_origin_mismatch", "wrong root"),
                ),
            ):
                record = profile_record(estimator, "standard")

        self.assertFalse(record["available"])
        self.assertEqual(record["path"], str(root.resolve()))
        self.assertEqual(record["error_code"], "estimator_origin_mismatch")
        self.assertEqual(record["message"], "wrong root")

    def test_local_profile_state_reports_both_profiles_and_remote_status(self):
        from app.config import AppConfig, EstimatorConfig
        from app.local_profile import local_profile_state

        available = {
            "available": True,
            "path": "/standard",
            "commit": "01234567",
            "dirty": False,
            "error_code": None,
            "message": None,
        }
        unavailable = {
            "available": False,
            "path": None,
            "commit": None,
            "dirty": None,
            "error_code": "estimator_profile_not_configured",
            "message": "not configured",
        }
        config = AppConfig(
            estimator=EstimatorConfig(
                sage_binary="custom-sage",
                remote_url="https://worker.invalid",
            )
        )
        with mock.patch(
            "app.local_profile.profile_record",
            side_effect=(available, unavailable),
        ) as record:
            state = local_profile_state(config)

        self.assertTrue(state["ok"])
        self.assertEqual(state["sage_binary"], "custom-sage")
        self.assertTrue(state["remote_configured"])
        self.assertEqual(state["profiles"]["standard"], available)
        self.assertEqual(state["profiles"]["enhanced"], unavailable)
        self.assertEqual(
            record.call_args_list,
            [
                mock.call(config.estimator, "standard"),
                mock.call(config.estimator, "enhanced"),
            ],
        )


class LocalProfilePersistenceTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        package = root / "estimator"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")

    def profile_payload(self, standard: Path, enhanced: Path | None = None) -> dict[str, object]:
        return {
            "sage_binary": "sage",
            "lattice_estimator_path": str(standard),
            "enhanced_lattice_estimator_path": str(enhanced) if enhanced else None,
        }

    def test_save_atomically_updates_only_profile_fields(self):
        from app.local_profile import GitMetadata, save_local_profile

        with TemporaryDirectory() as directory:
            root = Path(directory)
            standard = root / "standard"
            enhanced = root / "enhanced"
            self.make_repository(standard)
            self.make_repository(enhanced)
            config_path = root / "custom.json"
            original = {
                "estimator": {
                    "sage_binary": "old-sage",
                    "lattice_estimator_path": "/old/standard",
                    "enhanced_lattice_estimator_path": "/old/enhanced",
                    "default_timeout_seconds": 37,
                    "remote_url": "https://worker.invalid",
                    "remote_timeout_seconds": 123,
                },
                "llm": {"enabled": True, "model": "test-model"},
                "scripts": {"decrypt_error": ["./dfr.sage"]},
                "custom": {"preserve": [1, 2, 3]},
            }
            config_path.write_text(json.dumps(original), encoding="utf-8")

            with (
                mock.patch.dict(os.environ, {"EASYLATTICE_CONFIG": str(config_path)}),
                mock.patch("app.local_profile.shutil.which", return_value="/usr/bin/sage"),
                mock.patch("app.local_profile.run_origin_preflight", return_value={"ok": True}),
                mock.patch(
                    "app.local_profile.git_metadata",
                    return_value=GitMetadata("01234567", False, None),
                ),
                mock.patch("app.local_profile.os.replace", wraps=os.replace) as replace,
            ):
                state = save_local_profile(self.profile_payload(standard, enhanced))

            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["estimator"]["sage_binary"], "sage")
        self.assertEqual(saved["estimator"]["lattice_estimator_path"], str(standard.resolve()))
        self.assertEqual(
            saved["estimator"]["enhanced_lattice_estimator_path"],
            str(enhanced.resolve()),
        )
        self.assertEqual(saved["estimator"]["default_timeout_seconds"], 37)
        self.assertEqual(saved["estimator"]["remote_url"], "https://worker.invalid")
        self.assertEqual(saved["estimator"]["remote_timeout_seconds"], 123)
        self.assertEqual(saved["llm"], original["llm"])
        self.assertEqual(saved["scripts"], original["scripts"])
        self.assertEqual(saved["custom"], original["custom"])
        self.assertEqual(replace.call_count, 1)
        source, destination = replace.call_args.args
        self.assertEqual(Path(source).parent, config_path.parent)
        self.assertEqual(Path(destination), config_path)
        self.assertTrue(state["ok"])
        self.assertTrue(state["profiles"]["standard"]["available"])
        self.assertTrue(state["profiles"]["enhanced"]["available"])

    def test_save_leaves_original_bytes_after_validation_failure(self):
        from app.local_profile import LocalProfileError, save_local_profile

        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            original = b'{"custom": {"spacing": true}}\n'
            config_path.write_bytes(original)
            with mock.patch.dict(os.environ, {"EASYLATTICE_CONFIG": str(config_path)}):
                with self.assertRaises(LocalProfileError) as raised:
                    save_local_profile(self.profile_payload(root / "missing"))

            self.assertEqual(config_path.read_bytes(), original)
            self.assertEqual(list(root.glob(".config.json.*.tmp")), [])
        self.assertEqual(raised.exception.code, "estimator_path_invalid")

    def test_save_leaves_original_bytes_and_removes_temp_after_replace_failure(self):
        from app.local_profile import GitMetadata, LocalProfileError, save_local_profile

        with TemporaryDirectory() as directory:
            root = Path(directory)
            standard = root / "standard"
            self.make_repository(standard)
            config_path = root / "config.json"
            original = b'{"custom": {"spacing": true}}\n'
            config_path.write_bytes(original)
            with (
                mock.patch.dict(os.environ, {"EASYLATTICE_CONFIG": str(config_path)}),
                mock.patch("app.local_profile.shutil.which", return_value="/usr/bin/sage"),
                mock.patch("app.local_profile.run_origin_preflight", return_value={"ok": True}),
                mock.patch(
                    "app.local_profile.git_metadata",
                    return_value=GitMetadata("01234567", False, None),
                ),
                mock.patch("app.local_profile.os.replace", side_effect=OSError("denied")),
            ):
                with self.assertRaises(LocalProfileError) as raised:
                    save_local_profile(self.profile_payload(standard))

            self.assertEqual(config_path.read_bytes(), original)
            self.assertEqual(list(root.glob(".config.json.*.tmp")), [])
        self.assertEqual(raised.exception.code, "config_write_failed")


class LocalProfileRoutingTests(unittest.TestCase):
    def test_required_profile_supports_direct_and_nested_agent_payloads(self):
        from app.local_profile import required_profile_for_payload

        self.assertEqual(
            required_profile_for_payload(
                {
                    "problem": "rlwe",
                    "hardProblemCategory": "lwe",
                    "hardProblemVariant": "mlwe",
                    "useEstimator": True,
                }
            ),
            "enhanced",
        )
        self.assertEqual(
            required_profile_for_payload(
                {
                    "request": {
                        "problem": "ntru",
                        "hardProblemVariant": "ring",
                        "use_estimator": True,
                    }
                }
            ),
            "standard",
        )
        for problem in ("lwe", "lwr", "ntru"):
            with self.subTest(problem=problem):
                self.assertEqual(
                    required_profile_for_payload({"problem": problem, "useEstimator": True}),
                    "standard",
                )
        for variant in ("lwe", "lwr"):
            with self.subTest(frontend_problem="rlwe", variant=variant):
                self.assertEqual(
                    required_profile_for_payload(
                        {
                            "problem": "rlwe",
                            "hardProblemCategory": "lwe",
                            "hardProblemVariant": variant,
                            "useEstimator": True,
                        }
                    ),
                    "standard",
                )
        for variant in ("RLWE", "mlwe", "RLWR", "mlwr"):
            with self.subTest(variant=variant):
                self.assertEqual(
                    required_profile_for_payload(
                        {"hard_problem_variant": variant, "use_estimator": True}
                    ),
                    "enhanced",
                )
        self.assertIsNone(required_profile_for_payload({"useEstimator": False}))
        self.assertIsNone(required_profile_for_payload({"useEstimator": True, "problem": "sis"}))

    def test_require_available_profile_bypasses_local_checks_for_remote_worker(self):
        from app.config import AppConfig, EstimatorConfig
        from app.local_profile import require_available_profile

        config = AppConfig(estimator=EstimatorConfig(remote_url="https://worker.invalid"))
        with mock.patch("app.local_profile.profile_record") as record:
            result = require_available_profile(
                {"problem": "rlwe", "useEstimator": True},
                config,
            )

        self.assertIsNone(result)
        record.assert_not_called()

    def test_require_available_profile_raises_stable_local_error(self):
        from app.config import AppConfig
        from app.local_profile import LocalProfileError, require_available_profile

        unavailable = {
            "available": False,
            "path": None,
            "commit": None,
            "dirty": None,
            "error_code": "estimator_profile_not_configured",
            "message": "enhanced estimator path is not configured.",
        }
        with mock.patch("app.local_profile.profile_record", return_value=unavailable):
            with self.assertRaises(LocalProfileError) as raised:
                require_available_profile(
                    {"problem": "rlwe", "useEstimator": True},
                    AppConfig(),
                )

        self.assertEqual(raised.exception.code, "estimator_profile_not_configured")
        self.assertEqual(raised.exception.details["required_profile"], "enhanced")

    def test_error_exposes_separate_api_and_estimator_contracts(self):
        from app.local_profile import LocalProfileError

        error = LocalProfileError("stable_code", "safe message", required_profile="standard")
        self.assertEqual(
            error.as_result(),
            {
                "ok": False,
                "code": "stable_code",
                "message": "safe message",
                "required_profile": "standard",
            },
        )
        self.assertEqual(
            error.as_api_payload(),
            {
                "ok": False,
                "code": "stable_code",
                "error": "safe message",
                "required_profile": "standard",
            },
        )

if __name__ == "__main__":
    unittest.main()
