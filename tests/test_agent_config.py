import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.agent import recommend_with_agent
from app.config import ROOT, AppConfig, EstimatorConfig, LLMConfig, load_config, public_config
from app.estimator_process import (
    ESTIMATOR_ORIGIN_PREFLIGHT,
    estimator_profile_for,
    estimator_root,
    run_estimator,
)
from app.job_progress import progress_reporting
from app.llm_provider import sanitize_overrides
from app.local_profile import (
    ESTIMATOR_ORIGIN_PREFLIGHT as SHARED_ESTIMATOR_ORIGIN_PREFLIGHT,
    EstimatorRuntime,
    GitMetadata,
    LocalProfileError,
)
from app.server import cors_origin_for


def estimator_route_payload(profile="standard"):
    return {
        "problem": "lwe",
        "hard_problem_variant": "rlwe" if profile == "enhanced" else "lwe",
    }


class AgentConfigTests(unittest.TestCase):
    def test_default_agent_is_deterministic(self):
        result = recommend_with_agent({"targetSecurity": 128, "maxQBits": 24})

        self.assertEqual(result["agent"]["name"], "deterministic")
        self.assertFalse(result["agent"]["llm_used"])
        self.assertIn("recommendation", result)

    def test_llm_request_requires_enabled_config(self):
        config = AppConfig(llm=LLMConfig(enabled=False))

        with self.assertRaises(ValueError):
            recommend_with_agent(
                {
                    "useLLM": True,
                    "intent": "128 bit MATZOV RLWE recommendation",
                },
                config=config,
            )

    def test_legacy_use_llm_camel_case_still_works(self):
        config = AppConfig(llm=LLMConfig(enabled=False))

        with self.assertRaises(ValueError):
            recommend_with_agent(
                {
                    "useLlm": True,
                    "intent": "128 bit MATZOV RLWE recommendation",
                },
                config=config,
            )

    def test_public_config_exposes_llm_status_without_secret_fields(self):
        config = AppConfig(
            llm=LLMConfig(
                enabled=True,
                provider="openai-compatible",
                base_url="https://example.invalid/v1",
                model="test-model",
                api_key_env="EASYLATTICE_TEST_KEY",
                auth_prefix="Bearer ",
            )
        )

        with patch.dict(os.environ, {"EASYLATTICE_TEST_KEY": "secret"}, clear=False):
            data = public_config(config)

        self.assertTrue(data["llm"]["enabled"])
        self.assertTrue(data["llm"]["api_key_present"])
        self.assertTrue(data["llm"]["configured"])
        self.assertNotIn("api_key_env", data["llm"])
        self.assertNotIn("auth_prefix", data["llm"])

    def test_public_config_exposes_estimator_version_when_readable(self):
        with TemporaryDirectory() as tmpdir:
            estimator_dir = Path(tmpdir) / "estimator"
            estimator_dir.mkdir()
            (estimator_dir / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")

            data = public_config(
                AppConfig(estimator=EstimatorConfig(lattice_estimator_path=tmpdir))
            )

        self.assertEqual(data["estimator"]["version"], "1.2.3")

    def test_nested_non_git_estimator_uses_static_version(self):
        repository = Path(__file__).resolve().parents[1]
        with TemporaryDirectory(dir=repository, prefix=".nested-estimator-") as tmpdir:
            estimator_dir = Path(tmpdir) / "estimator"
            estimator_dir.mkdir()
            (estimator_dir / "__init__.py").write_text(
                '__version__ = "nested-static"\n',
                encoding="utf-8",
            )
            data = public_config(
                AppConfig(estimator=EstimatorConfig(lattice_estimator_path=tmpdir))
            )

        self.assertEqual(data["estimator"]["version"], "nested-static")
        self.assertEqual(
            data["estimator"]["profiles"]["standard"]["revision"],
            "nested-static",
        )

    def test_estimator_profiles_follow_problem_structure(self):
        for variant in ("lwe", "lwr"):
            self.assertEqual(estimator_profile_for("lwe", variant), "standard")
        for variant in ("rlwe", "mlwe", "rlwr", "mlwr"):
            self.assertEqual(estimator_profile_for("lwe", variant), "enhanced")
        self.assertEqual(estimator_profile_for("ntru", "ring"), "standard")
        self.assertEqual(estimator_profile_for("ntru", "matrix"), "standard")

        with self.assertRaises(ValueError):
            estimator_profile_for("ntru", "unsupported")
        with self.assertRaises(ValueError):
            estimator_profile_for("unsupported", "lwe")

    def test_estimator_profiles_report_static_versions_and_availability(self):
        with TemporaryDirectory() as standard, TemporaryDirectory() as enhanced:
            for root, version in ((standard, "standard-test"), (enhanced, "enhanced-test")):
                package = Path(root) / "estimator"
                package.mkdir()
                (package / "__init__.py").write_text(
                    f'__version__ = "{version}"\n',
                    encoding="utf-8",
                )
            config = AppConfig(
                estimator=EstimatorConfig(
                    lattice_estimator_path=standard,
                    enhanced_lattice_estimator_path=enhanced,
                )
            )
            data = public_config(config)

        self.assertEqual(estimator_root(config.estimator, "standard"), standard)
        self.assertEqual(estimator_root(config.estimator, "enhanced"), enhanced)
        self.assertEqual(data["estimator"]["profiles"]["standard"]["path"], standard)
        self.assertEqual(data["estimator"]["profiles"]["enhanced"]["path"], enhanced)
        self.assertEqual(
            data["estimator"]["profiles"]["standard"]["revision"],
            "standard-test",
        )
        self.assertEqual(
            data["estimator"]["profiles"]["enhanced"]["revision"],
            "enhanced-test",
        )
        self.assertTrue(data["estimator"]["profiles"]["standard"]["available"])
        self.assertTrue(data["estimator"]["profiles"]["enhanced"]["available"])

    def test_estimator_root_distinguishes_named_repo_from_package_path(self):
        with TemporaryDirectory() as tmpdir:
            named_root = Path(tmpdir) / "estimator"
            named_package = named_root / "estimator"
            named_package.mkdir(parents=True)
            (named_package / "__init__.py").write_text("", encoding="utf-8")

            direct_root = Path(tmpdir) / "direct"
            direct_package = direct_root / "estimator"
            direct_package.mkdir(parents=True)
            (direct_package / "__init__.py").write_text("", encoding="utf-8")

            config = AppConfig(
                estimator=EstimatorConfig(
                    lattice_estimator_path=str(named_root),
                    enhanced_lattice_estimator_path=str(direct_package),
                )
            )
            normalized_standard = estimator_root(config.estimator, "standard")
            normalized_enhanced = estimator_root(config.estimator, "enhanced")
            data = public_config(config)

        self.assertEqual(normalized_standard, str(named_root))
        self.assertEqual(normalized_enhanced, str(direct_root))
        self.assertEqual(
            data["estimator"]["profiles"]["standard"]["path"],
            str(named_root),
        )
        self.assertEqual(
            data["estimator"]["profiles"]["enhanced"]["path"],
            str(direct_root),
        )
        self.assertTrue(data["estimator"]["profiles"]["standard"]["available"])
        self.assertTrue(data["estimator"]["profiles"]["enhanced"]["available"])

    def test_enhanced_path_preserves_legacy_estimator_config_positions(self):
        config = EstimatorConfig(
            "sage-test",
            "/standard",
            17,
            13,
            "https://estimator.invalid",
            241,
            2.5,
        )

        self.assertEqual(config.default_timeout_seconds, 17)
        self.assertEqual(config.per_attack_timeout_seconds, 13)
        self.assertEqual(config.remote_url, "https://estimator.invalid")
        self.assertEqual(config.remote_timeout_seconds, 241)
        self.assertEqual(config.remote_poll_interval_seconds, 2.5)
        self.assertIsNone(config.enhanced_lattice_estimator_path)

    def test_enhanced_estimator_path_environment_override_is_loaded(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                '{"estimator": {"enhanced_lattice_estimator_path": "from-config"}}',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "EASYLATTICE_CONFIG": str(config_path),
                    "ENHANCED_LATTICE_ESTIMATOR_PATH": "from-environment",
                },
                clear=False,
            ):
                config = load_config()

        self.assertEqual(
            config.estimator.enhanced_lattice_estimator_path,
            "from-environment",
        )

    def test_run_estimator_copies_payload_for_remote_profile(self):
        payload = estimator_route_payload("enhanced") | {"n": 512}
        expected = {"ok": True, "raw": "unchanged"}
        config = AppConfig(
            estimator=EstimatorConfig(remote_url="https://estimator.invalid")
        )
        events = []

        def dispatch(**kwargs):
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].stage, "estimator_running")
            self.assertEqual(events[0].estimator_profile, "enhanced")
            self.assertIsNone(events[0].estimator_commit)
            return expected

        with patch(
            "app.estimator_process.estimate_remotely",
            side_effect=dispatch,
        ) as remote:
            with progress_reporting(events.append):
                result = run_estimator(payload, 17, config, "enhanced")

        self.assertIs(result, expected)
        self.assertEqual(
            payload,
            {"problem": "lwe", "hard_problem_variant": "rlwe", "n": 512},
        )
        self.assertEqual(
            remote.call_args.kwargs["payload"],
            {
                "problem": "lwe",
                "hard_problem_variant": "rlwe",
                "n": 512,
                "estimator_profile": "enhanced",
            },
        )

    def test_run_estimator_rejects_profile_variant_mismatch_before_dispatch(self):
        config = AppConfig(
            estimator=EstimatorConfig(remote_url="https://estimator.invalid")
        )
        events = []

        with patch("app.estimator_process.estimate_remotely") as remote:
            with progress_reporting(events.append):
                result = run_estimator(
                    {"problem": "lwe", "hard_problem_variant": "lwe"},
                    17,
                    config,
                    "enhanced",
                )

        self.assertEqual(result["code"], "invalid_estimator_route")
        self.assertIn("lwe/enhanced", result["message"])
        self.assertEqual(events, [])
        remote.assert_not_called()

    def test_estimator_process_reexports_shared_origin_preflight(self):
        self.assertIs(ESTIMATOR_ORIGIN_PREFLIGHT, SHARED_ESTIMATOR_ORIGIN_PREFLIGHT)

    def test_local_attempt_reports_selected_profile_and_commit_before_preflight(self):
        successful = {"ok": True, "result": {"bits": 128}}
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"ok": true, "result": {"bits": 128}}\n',
            stderr="",
        )

        for profile in ("standard", "enhanced"):
            with self.subTest(profile=profile), TemporaryDirectory() as root:
                runtime = EstimatorRuntime(
                    sage_binary="/test/sage",
                    root=Path(root),
                    environment={"PYTHONPATH": f"{root}{os.pathsep}{ROOT}"},
                )
                metadata = GitMetadata("01234567", False, None)
                events = []

                def preflight(selected_runtime, selected_timeout):
                    self.assertIs(selected_runtime, runtime)
                    self.assertEqual(selected_timeout, 17)
                    self.assertEqual(len(events), 1)
                    self.assertEqual(events[0].stage, "estimator_running")
                    self.assertEqual(events[0].estimator_profile, profile)
                    self.assertEqual(events[0].estimator_commit, "01234567")
                    return {"ok": True}

                config = AppConfig(estimator=EstimatorConfig())
                with patch(
                    "app.estimator_process.prepare_estimator_runtime",
                    return_value=runtime,
                ) as prepare, patch(
                    "app.estimator_process.git_metadata",
                    return_value=metadata,
                ) as metadata_for, patch(
                    "app.estimator_process.run_origin_preflight",
                    side_effect=preflight,
                ) as origin_preflight, patch(
                    "app.estimator_process.subprocess.run",
                    return_value=completed,
                ) as runner:
                    with progress_reporting(events.append):
                        result = run_estimator(
                            estimator_route_payload(profile),
                            17,
                            config,
                            profile,
                        )

                self.assertEqual(result, successful)
                prepare.assert_called_once_with(config.estimator, profile)
                metadata_for.assert_called_once_with(runtime.root)
                origin_preflight.assert_called_once_with(runtime, 17)
                runner.assert_called_once()

    def test_local_validation_failures_do_not_report_estimator_running(self):
        failures = (
            LocalProfileError("sage_not_found", "missing Sage"),
            LocalProfileError("estimator_path_invalid", "invalid estimator path"),
        )
        for failure in failures:
            with self.subTest(code=failure.code):
                events = []
                with patch(
                    "app.estimator_process.prepare_estimator_runtime",
                    side_effect=failure,
                ), patch("app.estimator_process.git_metadata") as metadata_for, patch(
                    "app.estimator_process.run_origin_preflight"
                ) as origin_preflight, patch(
                    "app.estimator_process.subprocess.run"
                ) as runner:
                    with progress_reporting(events.append):
                        result = run_estimator(
                            estimator_route_payload(),
                            5,
                            AppConfig(
                                estimator=EstimatorConfig(
                                    lattice_estimator_path="/configured"
                                )
                            ),
                            "standard",
                        )

                self.assertEqual(result["code"], failure.code)
                self.assertEqual(events, [])
                metadata_for.assert_not_called()
                origin_preflight.assert_not_called()
                runner.assert_not_called()

    def test_run_estimator_isolates_and_normalizes_selected_profile_root(self):
        payload = estimator_route_payload("enhanced") | {"n": 512}
        successful = {"ok": True, "result": {"bits": 128}}
        with TemporaryDirectory() as standard, TemporaryDirectory() as enhanced, TemporaryDirectory() as competing:
            for root in (standard, enhanced, competing):
                package = Path(root) / "estimator"
                package.mkdir()
                (package / "__init__.py").write_text("", encoding="utf-8")
            config = AppConfig(
                estimator=EstimatorConfig(
                    sage_binary="sage-test",
                    lattice_estimator_path=standard,
                    enhanced_lattice_estimator_path=str(Path(enhanced) / "estimator"),
                )
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"ok": true, "result": {"bits": 128}}\n',
                stderr="",
            )
            inherited_path = os.pathsep.join((competing, "shared"))
            with patch.dict(
                os.environ,
                {
                    "PYTHONPATH": inherited_path,
                    "EASYLATTICE_ESTIMATOR_ROOT": competing,
                },
                clear=True,
            ), patch(
                "app.local_profile.shutil.which",
                return_value="/test/sage",
            ), patch(
                "app.estimator_process.git_metadata",
                return_value=GitMetadata("01234567", False, None),
            ), patch(
                "app.estimator_process.run_origin_preflight",
                return_value={"ok": True},
            ) as preflight, patch(
                "app.estimator_process.subprocess.run",
                return_value=completed,
            ) as runner_process:
                result = run_estimator(payload, 17, config, "enhanced")
            normalized_enhanced = estimator_root(config.estimator, "enhanced")

        self.assertEqual(result, successful)
        self.assertEqual(
            payload,
            {"problem": "lwe", "hard_problem_variant": "rlwe", "n": 512},
        )
        self.assertEqual(normalized_enhanced, enhanced)
        preflight.assert_called_once()
        runner_process.assert_called_once()
        runner_call = runner_process.call_args
        runtime, preflight_timeout = preflight.call_args.args
        self.assertEqual(preflight_timeout, 17)
        self.assertEqual(runtime.sage_binary, "/test/sage")
        self.assertEqual(runtime.root, Path(enhanced))
        self.assertEqual(
            runner_call.args[0][0:2],
            ["/test/sage", "-python"],
        )
        self.assertTrue(runner_call.args[0][2].endswith("app/estimator_runner.py"))
        self.assertIs(runner_call.kwargs["env"], runtime.environment)
        self.assertEqual(
            runtime.environment["PYTHONPATH"].split(os.pathsep),
            [enhanced, str(ROOT)],
        )
        self.assertEqual(runtime.environment["PYTHONNOUSERSITE"], "1")
        self.assertEqual(
            runtime.environment["EASYLATTICE_ESTIMATOR_ROOT"],
            enhanced,
        )
        self.assertNotIn(competing, runtime.environment["PYTHONPATH"])
        self.assertEqual(
            runner_call.kwargs["input"],
            '{"problem": "lwe", "hard_problem_variant": "rlwe", "n": 512, '
            '"estimator_profile": "enhanced"}',
        )

    def test_run_estimator_rejects_invalid_or_mismatched_roots(self):
        with TemporaryDirectory() as invalid, TemporaryDirectory() as selected:
            selected_package = Path(selected) / "estimator"
            selected_package.mkdir()
            (selected_package / "__init__.py").write_text("", encoding="utf-8")

            events = []
            with patch("app.estimator_process.subprocess.run") as process:
                with progress_reporting(events.append):
                    result = run_estimator(
                        estimator_route_payload(),
                        5,
                        AppConfig(
                            estimator=EstimatorConfig(lattice_estimator_path=invalid)
                        ),
                        "standard",
                    )
            self.assertEqual(result["code"], "estimator_path_invalid")
            self.assertEqual(events, [])
            process.assert_not_called()

            with patch(
                "app.local_profile.shutil.which",
                return_value="/test/sage",
            ), patch(
                "app.estimator_process.git_metadata",
                return_value=GitMetadata("01234567", False, None),
            ), patch(
                "app.estimator_process.run_origin_preflight",
                side_effect=LocalProfileError(
                    "estimator_origin_mismatch",
                    "wrong estimator",
                ),
            ) as preflight, patch(
                "app.estimator_process.subprocess.run"
            ) as runner_process:
                with progress_reporting(events.append):
                    result = run_estimator(
                        estimator_route_payload(),
                        5,
                        AppConfig(
                            estimator=EstimatorConfig(lattice_estimator_path=selected)
                        ),
                        "standard",
                    )
            self.assertEqual(result["code"], "estimator_origin_mismatch")
            self.assertEqual(len(events), 1)
            preflight.assert_called_once()
            runner_process.assert_not_called()

    def test_estimator_origin_preflight_detects_competing_package(self):
        application_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as selected, TemporaryDirectory() as competing:
            for root in (selected, competing):
                package = Path(root) / "estimator"
                package.mkdir()
                (package / "__init__.py").write_text("", encoding="utf-8")

            def execute_preflight(python_path: str) -> dict:
                env = os.environ.copy()
                env["PYTHONPATH"] = python_path
                env["PYTHONNOUSERSITE"] = "1"
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        ESTIMATOR_ORIGIN_PREFLIGHT,
                        selected,
                        str(application_root),
                    ],
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                    env=env,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return json.loads(completed.stdout.strip().splitlines()[-1])

            mismatch = execute_preflight(competing)
            isolated = execute_preflight(selected)

        self.assertFalse(mismatch["ok"])
        self.assertEqual(mismatch["code"], "estimator_origin_mismatch")
        self.assertIn(str(Path(competing).resolve()), mismatch["message"])
        self.assertEqual(isolated, {"ok": True})

    def test_local_runner_preserves_post_preflight_origin_mismatch(self):
        with (
            TemporaryDirectory() as selected,
            TemporaryDirectory() as competing,
            TemporaryDirectory() as scripts,
        ):
            for root in (selected, competing):
                package = Path(root) / "estimator"
                package.mkdir()
                (package / "__init__.py").write_text("", encoding="utf-8")

            runner_executed = Path(scripts) / "runner-executed"
            sage = Path(scripts) / "sage"
            sage.write_text(
                "#!/bin/sh\n"
                "shift\n"
                "if [ \"$1\" = \"-c\" ]; then\n"
                f"  exec {shlex.quote(sys.executable)} \"$@\"\n"
                "fi\n"
                f": > {shlex.quote(str(runner_executed))}\n"
                f"PYTHONPATH={shlex.quote(competing)} "
                f"exec {shlex.quote(sys.executable)} \"$@\"\n",
                encoding="utf-8",
            )
            sage.chmod(0o755)
            config = AppConfig(
                estimator=EstimatorConfig(
                    sage_binary=str(sage),
                    lattice_estimator_path=selected,
                )
            )
            inherited = {
                "EASYLATTICE_ESTIMATOR_ROOT": competing,
                "PYTHONPATH": competing,
            }

            with patch.dict(os.environ, inherited, clear=True):
                result = run_estimator(estimator_route_payload(), 5, config, "standard")
            reached_runner = runner_executed.exists()

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "estimator_origin_mismatch")
        self.assertIn(str(Path(competing).resolve()), result["message"])
        self.assertIn(str(Path(selected).resolve()), result["message"])
        self.assertTrue(reached_runner)

    def test_run_estimator_returns_stable_local_failure_codes(self):
        with TemporaryDirectory() as root:
            package = Path(root) / "estimator"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")

            missing_sage = AppConfig(
                estimator=EstimatorConfig(
                    sage_binary="missing-sage",
                    lattice_estimator_path=root,
                )
            )
            with patch("app.local_profile.shutil.which", return_value=None):
                result = run_estimator(
                    estimator_route_payload(),
                    5,
                    missing_sage,
                    "standard",
                )
            self.assertEqual(result["code"], "sage_not_found")

            for profile in ("standard", "enhanced"):
                with self.subTest(profile=profile):
                    result = run_estimator(
                        estimator_route_payload(profile),
                        5,
                        AppConfig(),
                        profile,
                    )
                    self.assertEqual(
                        result["code"],
                        f"{profile}_estimator_not_configured",
                    )

            config = AppConfig(
                estimator=EstimatorConfig(
                    sage_binary="sage-test",
                    lattice_estimator_path=root,
                )
            )
            cases = (
                (
                    subprocess.TimeoutExpired(cmd=["sage"], timeout=5),
                    "estimator_timeout",
                ),
                (
                    subprocess.CompletedProcess([], 2, stdout="", stderr="failed\n"),
                    "estimator_process_failed",
                ),
                (
                    OSError("permission denied"),
                    "estimator_process_failed",
                ),
                (
                    subprocess.CompletedProcess([], 0, stdout="not-json\n", stderr=""),
                    "estimator_non_json",
                ),
                (
                    subprocess.CompletedProcess([], 0, stdout="[]\n", stderr=""),
                    "estimator_non_json",
                ),
            )
            for runner_result, code in cases:
                with self.subTest(code=code), patch(
                    "app.local_profile.shutil.which",
                    return_value="/test/sage",
                ), patch(
                    "app.estimator_process.run_origin_preflight",
                    return_value={"ok": True},
                ), patch(
                    "app.estimator_process.git_metadata",
                    return_value=GitMetadata("01234567", False, None),
                ), patch(
                    "app.estimator_process.subprocess.run"
                ) as runner:
                    if isinstance(runner_result, BaseException):
                        runner.side_effect = runner_result
                    else:
                        runner.return_value = runner_result
                    result = run_estimator(
                        estimator_route_payload(),
                        5,
                        config,
                        "standard",
                    )
                self.assertEqual(result["code"], code)

            preflight_failures = (
                (
                    LocalProfileError(
                        "estimator_preflight_timeout",
                        "Estimator validation timed out after 5s.",
                    ),
                    "estimator_timeout",
                ),
                (
                    LocalProfileError(
                        "estimator_preflight_failed",
                        "Estimator validation failed.",
                    ),
                    "estimator_process_failed",
                ),
                (
                    LocalProfileError(
                        "estimator_origin_mismatch",
                        "wrong estimator",
                    ),
                    "estimator_origin_mismatch",
                ),
            )
            for failure, code in preflight_failures:
                with self.subTest(preflight_code=failure.code), patch(
                    "app.local_profile.shutil.which",
                    return_value="/test/sage",
                ), patch(
                    "app.estimator_process.run_origin_preflight",
                    side_effect=failure,
                ), patch(
                    "app.estimator_process.git_metadata",
                    return_value=GitMetadata("01234567", False, None),
                ), patch("app.estimator_process.subprocess.run") as runner:
                    result = run_estimator(
                        estimator_route_payload(),
                        5,
                        config,
                        "standard",
                    )
                self.assertEqual(result["code"], code)
                runner.assert_not_called()

    def test_public_config_exposes_remote_estimator_status(self):
        data = public_config(
            AppConfig(
                estimator=EstimatorConfig(
                    remote_url="https://example-estimator.hf.space",
                    remote_timeout_seconds=240,
                )
            )
        )

        self.assertTrue(data["estimator"]["remote_configured"])
        self.assertEqual(data["estimator"]["remote_url"], "https://example-estimator.hf.space")

    def test_llm_overrides_are_whitelisted(self):
        overrides = sanitize_overrides(
            {
                "targetSecurity": 128,
                "ringFamily": "ternary",
                "api_key": "should-not-pass",
                "python": "should-not-pass",
            }
        )

        self.assertEqual(overrides, {"targetSecurity": 128, "ringFamily": "ternary"})

    def test_cors_origin_matching(self):
        self.assertEqual(cors_origin_for("https://icarid-liu.github.io", ["*"]), "*")
        self.assertEqual(
            cors_origin_for("https://icarid-liu.github.io", ["https://icarid-liu.github.io"]),
            "https://icarid-liu.github.io",
        )
        self.assertIsNone(cors_origin_for("https://example.invalid", ["https://icarid-liu.github.io"]))


if __name__ == "__main__":
    unittest.main()
