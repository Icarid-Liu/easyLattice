import importlib.util
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.estimator_runner import run_lwe, run_lwe_attack, summarize_attacks


class FakeLWE:
    calls = []
    failures = set()

    class Parameters:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def normalize(self):
            return self

    @classmethod
    def _record(cls, name, kwargs):
        cls.calls.append((name, kwargs))
        if (kwargs["red_cost_model"], name) in cls.failures:
            raise RuntimeError(f"{name} failed")
        return {"rop": 140.0 - len(cls.calls)}

    @classmethod
    def primal_usvp(cls, _params, **kwargs):
        return cls._record("usvp", kwargs)

    @classmethod
    def dual_hybrid(cls, _params, **kwargs):
        return cls._record("dual_hybrid", kwargs)

    @classmethod
    def primal_hybrid(cls, _params, **kwargs):
        return cls._record("bdd_hybrid", kwargs)


class FakeND:
    @staticmethod
    def CenteredBinomial(eta):
        return ("centered_binomial", eta)


def load_space_app():
    path = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "huggingface-estimator"
        / "space_app.py"
    )
    spec = importlib.util.spec_from_file_location("test_huggingface_estimator_space_app", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class EstimatorRunnerTests(unittest.TestCase):
    def setUp(self):
        FakeLWE.calls.clear()
        FakeLWE.failures.clear()

    def test_baseline_attacks_receive_exact_arguments(self):
        params = object()
        model = object()

        run_lwe_attack(FakeLWE, params, "usvp", model, "classical", "standard", 512)
        run_lwe_attack(FakeLWE, params, "dual_hybrid", model, "quantum", "enhanced", 512)

        self.assertEqual(
            FakeLWE.calls,
            [
                ("usvp", {"red_cost_model": model, "red_shape_model": "gsa"}),
                ("dual_hybrid", {"red_cost_model": model}),
            ],
        )

    def test_enhanced_bdd_hybrid_receives_ring_arguments_and_quantum_grover(self):
        params = object()
        model = object()

        run_lwe_attack(FakeLWE, params, "bdd_hybrid", model, "classical", "enhanced", 512)
        run_lwe_attack(FakeLWE, params, "bdd_hybrid", model, "quantum", "enhanced", 512)

        self.assertEqual(
            FakeLWE.calls,
            [
                (
                    "bdd_hybrid",
                    {
                        "red_cost_model": model,
                        "mitm": False,
                        "babai": False,
                        "deg_ring": 512,
                        "structure_leverage": True,
                    },
                ),
                (
                    "bdd_hybrid",
                    {
                        "red_cost_model": model,
                        "mitm": False,
                        "babai": False,
                        "deg_ring": 512,
                        "structure_leverage": True,
                        "Grover": True,
                    },
                ),
            ],
        )

    def test_standard_bdd_hybrid_omits_enhanced_only_arguments(self):
        model = object()

        run_lwe_attack(FakeLWE, object(), "bdd_hybrid", model, "quantum", "standard", 512)

        self.assertEqual(
            FakeLWE.calls[0],
            (
                "bdd_hybrid",
                {"red_cost_model": model, "mitm": False, "babai": False},
            ),
        )

    def test_unsupported_attack_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "Unsupported LWE attack: unknown"):
            run_lwe_attack(FakeLWE, object(), "unknown", object(), "classical", "standard", 512)

    def test_summaries_distinguish_partial_and_complete_results(self):
        partial = summarize_attacks(
            {
                "usvp": {"ok": False, "message": "timeout"},
                "dual_hybrid": {"ok": True, "rop_bits": 133.5},
                "bdd_hybrid": {"ok": False, "message": "unsupported"},
            }
        )
        complete = summarize_attacks(
            {
                "usvp": {"ok": True, "rop_bits": 140.0},
                "dual_hybrid": {"ok": True, "rop_bits": 133.5},
                "bdd_hybrid": {"ok": True, "rop_bits": 136.0},
            }
        )
        failed = summarize_attacks(
            {
                "usvp": {"ok": False, "message": "timeout"},
                "dual_hybrid": {"ok": False, "message": "timeout"},
                "bdd_hybrid": {"ok": False, "message": "timeout"},
            }
        )

        self.assertTrue(partial["ok"])
        self.assertFalse(partial["complete"])
        self.assertEqual(partial["best_attack"], "dual_hybrid")
        self.assertEqual(partial["min_bits"], 133.5)
        self.assertTrue(complete["ok"])
        self.assertTrue(complete["complete"])
        self.assertFalse(failed["ok"])
        self.assertFalse(failed["complete"])

    def test_run_lwe_reports_partial_top_level_contract_and_echoes_parameters(self):
        FakeLWE.failures = {
            (model, "bdd_hybrid")
            for model in (
                "matzov-classical",
                "matzov-quantum",
                "adps16-classical",
                "adps16-quantum",
            )
        }

        result = self.run_fake_lwe()

        self.assertTrue(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["estimator_profile"], "enhanced")
        self.assertEqual(result["estimator_commit"], "abc1234")
        self.assertEqual(result["hard_problem_variant"], "rlwe")
        self.assertEqual(result["ring_degree"], 512)
        self.assertIs(result["modes"], result["models"]["adps16"])
        self.assertEqual(
            set(result),
            {
                "ok",
                "complete",
                "estimator_profile",
                "estimator_commit",
                "hard_problem_variant",
                "ring_degree",
                "modes",
                "models",
                "parameters",
            },
        )
        self.assertEqual(result["parameters"]["estimator_profile"], "enhanced")
        self.assertEqual(result["parameters"]["hard_problem_variant"], "rlwe")
        self.assertEqual(result["parameters"]["ring_degree"], 512)
        for family in result["models"].values():
            for mode in family.values():
                self.assertTrue(mode["ok"])
                self.assertFalse(mode["complete"])
                self.assertEqual(set(mode["attacks"]), {"usvp", "dual_hybrid", "bdd_hybrid"})

    def test_run_lwe_top_level_ok_requires_a_success_in_every_model_mode(self):
        FakeLWE.failures = {
            ("matzov-quantum", attack)
            for attack in ("usvp", "dual_hybrid", "bdd_hybrid")
        }

        result = self.run_fake_lwe()

        self.assertFalse(result["ok"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["models"]["matzov"]["quantum"]["ok"])
        self.assertTrue(result["models"]["matzov"]["classical"]["ok"])
        self.assertTrue(result["models"]["adps16"]["quantum"]["ok"])

    def test_run_lwe_top_level_complete_requires_every_attack_to_succeed(self):
        result = self.run_fake_lwe()

        self.assertTrue(result["ok"])
        self.assertTrue(result["complete"])
        self.assertEqual(set(result["models"]), {"matzov", "adps16"})
        for family in result["models"].values():
            for mode in family.values():
                self.assertTrue(mode["complete"])

    def run_fake_lwe(self):
        estimator_module = types.ModuleType("estimator")
        estimator_module.LWE = FakeLWE
        estimator_module.ND = FakeND
        models = {
            "matzov": {
                "classical": "matzov-classical",
                "quantum": "matzov-quantum",
            },
            "adps16": {
                "classical": "adps16-classical",
                "quantum": "adps16-quantum",
            },
        }
        payload = {
            "problem": "lwe",
            "n": 512,
            "q": 12289,
            "distribution": {
                "name": "CBD(2)",
                "estimator": {"type": "centered_binomial", "eta": 2},
            },
            "estimator_profile": "enhanced",
            "hard_problem_variant": "rlwe",
            "ring_degree": 512,
            "per_attack_timeout": 1,
        }
        with (
            patch.dict(sys.modules, {"estimator": estimator_module}),
            patch("app.estimator_runner.reduction_model_variants", return_value=models),
            patch("app.estimator_runner.cost_to_json", side_effect=lambda cost: {"rop_bits": cost["rop"]}),
            patch("app.estimator_runner.estimator_commit", return_value="abc1234"),
        ):
            return run_lwe(payload)


class EstimatorSpaceAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.space_app = load_space_app()

    @staticmethod
    def payload(profile="standard"):
        return {
            "problem": "lwe",
            "n": 512,
            "q": 12289,
            "distribution": {
                "estimator": {"type": "centered_binomial", "eta": 2},
            },
            "estimator_profile": profile,
        }

    def test_invalid_profile_is_rejected_with_stable_error(self):
        with self.assertRaisesRegex(
            ValueError,
            "^estimator_profile must be standard or enhanced\\.$",
        ):
            self.space_app.validate_payload(self.payload("experimental"))

        with patch.object(self.space_app.subprocess, "run") as run:
            result = self.space_app.run_estimator_subprocess(self.payload("experimental"), 5)
        self.assertEqual(
            result,
            {
                "ok": False,
                "code": "invalid_estimator_profile",
                "message": "estimator_profile must be standard or enhanced.",
            },
        )
        run.assert_not_called()

    def test_subprocess_reports_missing_selected_estimator_root(self):
        cases = (
            (
                "standard",
                {"ENHANCED_LATTICE_ESTIMATOR_PATH": "/opt/enhanced-estimator"},
            ),
            (
                "enhanced",
                {"LATTICE_ESTIMATOR_PATH": "/opt/standard-estimator"},
            ),
        )

        for profile, environment in cases:
            with self.subTest(profile=profile), patch.dict(
                os.environ,
                environment,
                clear=True,
            ), patch.object(self.space_app.subprocess, "run") as run:
                result = self.space_app.run_estimator_subprocess(self.payload(profile), 5)

            self.assertEqual(
                result,
                {
                    "ok": False,
                    "code": f"{profile}_estimator_not_configured",
                    "message": f"{profile} estimator path is not configured.",
                },
            )
            run.assert_not_called()

    def test_subprocess_uses_only_selected_estimator_root(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"ok": true}\n',
            stderr="",
        )
        paths = {
            "LATTICE_ESTIMATOR_PATH": "/opt/standard-estimator",
            "ENHANCED_LATTICE_ESTIMATOR_PATH": "/opt/enhanced-estimator",
            "PYTHONPATH": "/ambient/import/path",
        }

        with patch.dict(os.environ, paths), patch.object(
            self.space_app.subprocess,
            "run",
            return_value=completed,
        ) as run:
            for profile, expected in (
                ("standard", "/opt/standard-estimator"),
                ("enhanced", "/opt/enhanced-estimator"),
            ):
                with self.subTest(profile=profile):
                    result = self.space_app.run_estimator_subprocess(self.payload(profile), 5)
                    self.assertTrue(result["ok"])
                    environment = run.call_args.kwargs["env"]
                    self.assertEqual(environment["PYTHONPATH"], expected)
                    self.assertEqual(environment["PYTHONNOUSERSITE"], "1")

    def test_docker_images_install_enhanced_estimator_profile(self):
        root = Path(__file__).resolve().parents[1]
        for relative_path in (
            "deploy/huggingface-estimator/Dockerfile",
            "deploy/huggingface-live/Dockerfile",
        ):
            with self.subTest(path=relative_path):
                contents = (root / relative_path).read_text(encoding="utf-8")
                self.assertIn(
                    "https://github.com/identitymapping/enhanced_lattice-estimator.git "
                    "/opt/enhanced-lattice-estimator",
                    contents,
                )
                self.assertIn(
                    "ENV ENHANCED_LATTICE_ESTIMATOR_PATH=/opt/enhanced-lattice-estimator",
                    contents,
                )


if __name__ == "__main__":
    unittest.main()
