import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.agent import recommend_with_agent
from app.config import AppConfig, EstimatorConfig, LLMConfig, load_config, public_config
from app.estimator_process import estimator_profile_for, estimator_root, run_estimator
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

    def test_run_estimator_uses_only_selected_local_profile_root(self):
        payload = {"problem": "lwe", "n": 512}
        successful = {"ok": True, "result": {"bits": 128}}
        with TemporaryDirectory() as standard, TemporaryDirectory() as enhanced:
            config = AppConfig(
                estimator=EstimatorConfig(
                    sage_binary="sage-test",
                    lattice_estimator_path=standard,
                    enhanced_lattice_estimator_path=enhanced,
                )
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"ok": true, "result": {"bits": 128}}\n',
                stderr="",
            )
            with patch.dict(os.environ, {"PYTHONPATH": "shared"}, clear=True), patch(
                "app.estimator_process.shutil.which",
                return_value="/test/sage",
            ), patch(
                "app.estimator_process.subprocess.run",
                return_value=completed,
            ) as process:
                result = run_estimator(payload, 17, config, "enhanced")

        self.assertEqual(result, successful)
        self.assertEqual(payload, {"problem": "lwe", "n": 512})
        self.assertEqual(
            process.call_args.args[0][0:2],
            ["/test/sage", "-python"],
        )
        python_path = process.call_args.kwargs["env"]["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(python_path, [enhanced, "shared"])
        self.assertNotIn(standard, python_path)
        self.assertEqual(
            process.call_args.kwargs["input"],
            '{"problem": "lwe", "n": 512, "estimator_profile": "enhanced"}',
        )

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

        config = AppConfig(
            estimator=EstimatorConfig(
                sage_binary="sage-test",
                lattice_estimator_path="/test/estimator",
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
                subprocess.CompletedProcess([], 0, stdout="not-json\n", stderr=""),
                "estimator_non_json",
            ),
        )
        for process_result, code in cases:
            with self.subTest(code=code), patch(
                "app.estimator_process.shutil.which",
                return_value="/test/sage",
            ), patch("app.estimator_process.subprocess.run") as process:
                if isinstance(process_result, Exception):
                    process.side_effect = process_result
                else:
                    process.return_value = process_result
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
