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
from app.config import AppConfig, EstimatorConfig, LLMConfig, load_config, public_config
from app.estimator_process import (
    ESTIMATOR_ORIGIN_PREFLIGHT,
    estimator_profile_for,
    estimator_root,
    run_estimator,
)
from app.llm_provider import sanitize_overrides
from app.server import cors_origin_for


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
        payload = {"problem": "lwe", "n": 512}
        expected = {"ok": True, "raw": "unchanged"}
        config = AppConfig(
            estimator=EstimatorConfig(remote_url="https://estimator.invalid")
        )

        with patch(
            "app.estimator_process.estimate_remotely",
            return_value=expected,
        ) as remote:
            result = run_estimator(payload, 17, config, "enhanced")

        self.assertIs(result, expected)
        self.assertEqual(payload, {"problem": "lwe", "n": 512})
        self.assertEqual(
            remote.call_args.kwargs["payload"],
            {"problem": "lwe", "n": 512, "estimator_profile": "enhanced"},
        )

    def test_run_estimator_isolates_and_normalizes_selected_profile_root(self):
        payload = {"problem": "lwe", "n": 512}
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
            preflight = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"ok": true}\n',
                stderr="",
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
                "app.estimator_process.shutil.which",
                return_value="/test/sage",
            ), patch(
                "app.estimator_process.subprocess.run",
                side_effect=(preflight, completed),
            ) as process:
                result = run_estimator(payload, 17, config, "enhanced")
            normalized_enhanced = estimator_root(config.estimator, "enhanced")

        self.assertEqual(result, successful)
        self.assertEqual(payload, {"problem": "lwe", "n": 512})
        self.assertEqual(normalized_enhanced, enhanced)
        self.assertEqual(process.call_count, 2)
        preflight_call, runner_call = process.call_args_list
        self.assertEqual(
            preflight_call.args[0][0:3],
            ["/test/sage", "-python", "-c"],
        )
        self.assertEqual(
            runner_call.args[0][0:2],
            ["/test/sage", "-python"],
        )
        self.assertTrue(runner_call.args[0][2].endswith("app/estimator_runner.py"))
        for call in (preflight_call, runner_call):
            self.assertEqual(call.kwargs["env"]["PYTHONPATH"], enhanced)
            self.assertEqual(call.kwargs["env"]["PYTHONNOUSERSITE"], "1")
            self.assertEqual(
                call.kwargs["env"]["EASYLATTICE_ESTIMATOR_ROOT"],
                enhanced,
            )
            self.assertNotIn(competing, call.kwargs["env"]["PYTHONPATH"])
        self.assertEqual(
            runner_call.kwargs["input"],
            '{"problem": "lwe", "n": 512, "estimator_profile": "enhanced"}',
        )

    def test_run_estimator_rejects_invalid_or_mismatched_roots(self):
        with TemporaryDirectory() as invalid, TemporaryDirectory() as selected:
            selected_package = Path(selected) / "estimator"
            selected_package.mkdir()
            (selected_package / "__init__.py").write_text("", encoding="utf-8")

            with patch(
                "app.estimator_process.shutil.which",
                return_value="/test/sage",
            ), patch("app.estimator_process.subprocess.run") as process:
                result = run_estimator(
                    {},
                    5,
                    AppConfig(
                        estimator=EstimatorConfig(lattice_estimator_path=invalid)
                    ),
                    "standard",
                )
            self.assertEqual(result["code"], "estimator_path_invalid")
            process.assert_not_called()

            mismatch = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    '{"ok": false, "code": "estimator_origin_mismatch", '
                    '"message": "wrong estimator"}\n'
                ),
                stderr="",
            )
            with patch(
                "app.estimator_process.shutil.which",
                return_value="/test/sage",
            ), patch(
                "app.estimator_process.subprocess.run",
                return_value=mismatch,
            ) as process:
                result = run_estimator(
                    {},
                    5,
                    AppConfig(
                        estimator=EstimatorConfig(lattice_estimator_path=selected)
                    ),
                    "standard",
                )
            self.assertEqual(result["code"], "estimator_origin_mismatch")
            self.assertEqual(process.call_count, 1)

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
                result = run_estimator({}, 5, config, "standard")
            reached_runner = runner_executed.exists()

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "estimator_origin_mismatch")
        self.assertIn(str(Path(competing).resolve()), result["message"])
        self.assertIn(str(Path(selected).resolve()), result["message"])
        self.assertTrue(reached_runner)

    def test_run_estimator_returns_stable_local_failure_codes(self):
        missing_sage = AppConfig(estimator=EstimatorConfig(sage_binary="missing-sage"))
        with patch("app.estimator_process.shutil.which", return_value=None):
            result = run_estimator({}, 5, missing_sage, "standard")
        self.assertEqual(result["code"], "sage_not_found")

        with patch("app.estimator_process.shutil.which", return_value="/test/sage"):
            for profile in ("standard", "enhanced"):
                with self.subTest(profile=profile):
                    result = run_estimator({}, 5, AppConfig(), profile)
                    self.assertEqual(
                        result["code"],
                        f"{profile}_estimator_not_configured",
                    )

        with TemporaryDirectory() as root:
            package = Path(root) / "estimator"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            config = AppConfig(
                estimator=EstimatorConfig(
                    sage_binary="sage-test",
                    lattice_estimator_path=root,
                )
            )
            preflight = subprocess.CompletedProcess(
                [],
                0,
                stdout='{"ok": true}\n',
                stderr="",
            )
            cases = (
                (
                    (preflight, subprocess.TimeoutExpired(cmd=["sage"], timeout=5)),
                    "estimator_timeout",
                ),
                (
                    (
                        preflight,
                        subprocess.CompletedProcess([], 2, stdout="", stderr="failed\n"),
                    ),
                    "estimator_process_failed",
                ),
                (
                    (preflight, OSError("permission denied")),
                    "estimator_process_failed",
                ),
                (
                    (
                        preflight,
                        subprocess.CompletedProcess([], 0, stdout="not-json\n", stderr=""),
                    ),
                    "estimator_non_json",
                ),
                (
                    (
                        preflight,
                        subprocess.CompletedProcess([], 0, stdout="[]\n", stderr=""),
                    ),
                    "estimator_non_json",
                ),
            )
            for process_results, code in cases:
                with self.subTest(code=code), patch(
                    "app.estimator_process.shutil.which",
                    return_value="/test/sage",
                ), patch(
                    "app.estimator_process.subprocess.run",
                    side_effect=process_results,
                ):
                    result = run_estimator({}, 5, config, "standard")
                self.assertEqual(result["code"], code)

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
