import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.agent import recommend_with_agent
from app.config import AppConfig, EstimatorConfig, LLMConfig, public_config
from app.llm_provider import sanitize_overrides


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


if __name__ == "__main__":
    unittest.main()
