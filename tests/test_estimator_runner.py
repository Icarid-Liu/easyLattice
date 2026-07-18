import importlib.util
from io import BytesIO
import json
import os
import re
import shlex
import subprocess
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.estimator_runner import (
    attack_result,
    run_lwe,
    run_lwe_attack,
    run_ntru,
    summarize_attacks,
)
from app.estimator_contract import (
    EstimatorRouteError,
    ntru_type_for_variant,
    structure_correction_metadata,
)


FAKE_OO = object()


def fake_cost_to_json(cost):
    fields = {}
    if "rop" in cost:
        fields["rop_bits"] = None if cost["rop"] is FAKE_OO else cost["rop"]
    return fields


class FakeLWE:
    calls = []
    costs = {}
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
        if name in cls.costs:
            return cls.costs[name]
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


class FakeNTRU:
    estimates = {}

    class Parameters:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def normalize(self):
            return self

    @classmethod
    def estimate(cls, _params, **_kwargs):
        return dict(cls.estimates)


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
        FakeLWE.costs.clear()
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

    def test_runner_rejects_enhanced_unstructured_route_before_attack_execution(self):
        payload = self.fake_lwe_payload(profile="enhanced", variant="lwe")
        estimator_module = types.ModuleType("estimator")
        estimator_module.LWE = FakeLWE
        estimator_module.ND = FakeND

        with patch.dict(sys.modules, {"estimator": estimator_module}):
            with self.assertRaises(EstimatorRouteError) as raised:
                run_lwe(payload)

        self.assertEqual(raised.exception.code, "invalid_estimator_route")
        self.assertIn("lwe/enhanced", raised.exception.message)
        self.assertEqual(FakeLWE.calls, [])

    def test_ntru_variant_type_mapping_covers_all_circulant_families(self):
        self.assertEqual(ntru_type_for_variant("matrix"), "matrix")
        for variant in ("ring", "hps", "hrss", "ntru_prime"):
            with self.subTest(variant=variant):
                self.assertEqual(ntru_type_for_variant(variant), "circulant")

    def test_runner_rejects_contradictory_ntru_type_before_attack_execution(self):
        payload = {
            "problem": "ntru",
            "estimator_profile": "standard",
            "hard_problem_variant": "ring",
            "ntru_type": "matrix",
        }

        with self.assertRaises(EstimatorRouteError) as raised:
            run_ntru(payload)

        self.assertEqual(raised.exception.code, "invalid_estimator_route")
        self.assertIn("requires ntru_type=circulant", raised.exception.message)

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

    def test_attack_result_rejects_missing_rop(self):
        self.assertEqual(
            attack_result({}),
            {
                "ok": False,
                "code": "invalid_attack_cost",
                "message": "attack estimate returned no finite rop",
                "summary": "{}",
            },
        )

    def test_attack_result_rejects_invalid_raw_rop_before_log_conversion(self):
        for rop in (None, 0, -1, True, False, float("inf"), float("nan"), FAKE_OO):
            with self.subTest(rop=rop):
                result = attack_result({"rop": rop})
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], "invalid_attack_cost")

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

    def test_structured_attacks_report_honest_structure_correction_metadata(self):
        result = self.run_fake_lwe()

        for family in result["models"].values():
            for mode in family.values():
                for attack, attack_result in mode["attacks"].items():
                    self.assertEqual(
                        attack_result["structure_correction"],
                        structure_correction_metadata(attack, "enhanced", "rlwe"),
                    )
                self.assertFalse(
                    mode["attacks"]["dual_hybrid"]["structure_correction"]["available"]
                )
                self.assertFalse(
                    mode["attacks"]["dual_hybrid"]["structure_correction"]["applied"]
                )
                self.assertTrue(
                    mode["attacks"]["bdd_hybrid"]["structure_correction"]["applied"]
                )

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

    def test_structured_run_stays_partial_when_every_attack_returns_finite_cost(self):
        result = self.run_fake_lwe()

        self.assertTrue(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(set(result["models"]), {"matzov", "adps16"})
        for family in result["models"].values():
            for mode in family.values():
                self.assertFalse(mode["complete"])

    def test_standard_run_is_complete_when_every_attack_returns_finite_cost(self):
        result = self.run_fake_lwe(profile="standard", variant="lwe")

        self.assertTrue(result["ok"])
        self.assertTrue(result["complete"])
        for family in result["models"].values():
            for mode in family.values():
                self.assertTrue(mode["complete"])
                for attack_result in mode["attacks"].values():
                    self.assertEqual(
                        attack_result["structure_correction"]["code"],
                        "structure_correction_not_applicable",
                    )

    def test_unavailable_structured_dual_cost_is_inspection_only(self):
        FakeLWE.costs.update(
            {
                "usvp": {"rop": 160.0},
                "dual_hybrid": {"rop": 100.0},
                "bdd_hybrid": {"rop": 150.0},
            }
        )

        result = self.run_fake_lwe()

        for family in result["models"].values():
            for mode in family.values():
                self.assertEqual(mode["attacks"]["dual_hybrid"]["rop_bits"], 100.0)
                self.assertEqual(mode["best_attack"], "bdd_hybrid")
                self.assertEqual(mode["min_bits"], 150.0)

    def test_unavailable_structured_dual_cannot_make_a_mode_usable_alone(self):
        FakeLWE.failures = {
            (model, attack)
            for model in (
                "matzov-classical",
                "matzov-quantum",
                "adps16-classical",
                "adps16-quantum",
            )
            for attack in ("usvp", "bdd_hybrid")
        }

        result = self.run_fake_lwe()

        self.assertFalse(result["ok"])
        self.assertFalse(result["complete"])
        for family in result["models"].values():
            for mode in family.values():
                self.assertFalse(mode["ok"])
                self.assertNotIn("min_bits", mode)
                self.assertTrue(mode["attacks"]["dual_hybrid"]["ok"])

    def test_run_lwe_rejects_oo_and_non_finite_attack_costs(self):
        FakeLWE.costs.update(
            {
                "usvp": {"rop": FAKE_OO},
                "dual_hybrid": {"rop": float("inf")},
                "bdd_hybrid": {"rop": 128.0},
            }
        )

        result = self.run_fake_lwe()

        self.assertTrue(result["ok"])
        self.assertFalse(result["complete"])
        for family in result["models"].values():
            for mode in family.values():
                self.assertTrue(mode["ok"])
                self.assertFalse(mode["complete"])
                self.assertTrue(mode["attacks"]["bdd_hybrid"]["ok"])
                for attack in ("usvp", "dual_hybrid"):
                    self.assertFalse(mode["attacks"][attack]["ok"])
                    self.assertEqual(
                        mode["attacks"][attack]["code"],
                        "invalid_attack_cost",
                    )

    def test_run_ntru_marks_omitted_attacks_as_partial_failures(self):
        FakeNTRU.estimates = {"usvp": {"rop": 132.0}}

        result = self.run_fake_ntru()

        self.assertTrue(result["ok"])
        self.assertFalse(result["complete"])
        for family in result["models"].values():
            for mode in family.values():
                self.assertTrue(mode["ok"])
                self.assertFalse(mode["complete"])
                self.assertEqual(
                    set(mode["attacks"]),
                    {"usvp", "dsd", "bdd", "bdd_hybrid", "bdd_mitm_hybrid"},
                )
                self.assertTrue(mode["attacks"]["usvp"]["ok"])
                for attack in ("dsd", "bdd", "bdd_hybrid", "bdd_mitm_hybrid"):
                    self.assertEqual(
                        mode["attacks"][attack],
                        {
                            "ok": False,
                            "code": "attack_not_returned",
                            "message": "estimator omitted attack result",
                        },
                    )

    def test_run_ntru_rejects_none_oo_and_non_finite_attack_costs(self):
        FakeNTRU.estimates = {
            "usvp": {"rop": 132.0},
            "dsd": {"rop": None},
            "bdd": {"rop": FAKE_OO},
            "bdd_hybrid": {"rop": float("inf")},
            "bdd_mitm_hybrid": {"rop": float("nan")},
        }

        result = self.run_fake_ntru()

        self.assertTrue(result["ok"])
        self.assertFalse(result["complete"])
        for family in result["models"].values():
            for mode in family.values():
                self.assertTrue(mode["ok"])
                self.assertFalse(mode["complete"])
                self.assertTrue(mode["attacks"]["usvp"]["ok"])
                for attack in ("dsd", "bdd", "bdd_hybrid", "bdd_mitm_hybrid"):
                    self.assertFalse(mode["attacks"][attack]["ok"])
                    self.assertEqual(
                        mode["attacks"][attack]["code"],
                        "invalid_attack_cost",
                    )

    def test_runner_process_rejects_competing_estimator_origin(self):
        runner = Path(__file__).resolve().parents[1] / "app" / "estimator_runner.py"
        with TemporaryDirectory() as selected, TemporaryDirectory() as competing:
            for root in (selected, competing):
                package = Path(root) / "estimator"
                package.mkdir()
                (package / "__init__.py").write_text("", encoding="utf-8")

            environment = os.environ.copy()
            environment.update(
                {
                    "EASYLATTICE_ESTIMATOR_ROOT": selected,
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONPATH": competing,
                }
            )
            completed = subprocess.run(
                [sys.executable, str(runner)],
                input=json.dumps({
                    "problem": "lwe",
                    "estimator_profile": "standard",
                    "hard_problem_variant": "lwe",
                }),
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
                env=environment,
            )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "estimator_origin_mismatch")
        self.assertIn(str(Path(competing).resolve()), result["message"])
        self.assertIn(str(Path(selected).resolve()), result["message"])

    def test_runner_process_returns_stable_code_for_invalid_route(self):
        runner = Path(__file__).resolve().parents[1] / "app" / "estimator_runner.py"
        completed = subprocess.run(
            [sys.executable, str(runner)],
            input=json.dumps({
                "problem": "lwe",
                "estimator_profile": "enhanced",
                "hard_problem_variant": "lwe",
            }),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["code"], "invalid_estimator_route")

    @staticmethod
    def fake_lwe_payload(profile="enhanced", variant="rlwe"):
        return {
            "problem": "lwe",
            "n": 512,
            "q": 12289,
            "distribution": {
                "name": "CBD(2)",
                "estimator": {"type": "centered_binomial", "eta": 2},
            },
            "estimator_profile": profile,
            "hard_problem_variant": variant,
            "ring_degree": 512,
            "per_attack_timeout": 1,
        }

    def run_fake_lwe(self, profile="enhanced", variant="rlwe"):
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
        payload = self.fake_lwe_payload(profile=profile, variant=variant)
        with (
            patch.dict(sys.modules, {"estimator": estimator_module}),
            patch("app.estimator_runner.reduction_model_variants", return_value=models),
            patch("app.estimator_runner.cost_to_json", side_effect=fake_cost_to_json),
            patch("app.estimator_runner.estimator_commit", return_value="abc1234"),
        ):
            return run_lwe(payload)

    def run_fake_ntru(self):
        estimator_module = types.ModuleType("estimator")
        estimator_module.NTRU = FakeNTRU
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
        distribution = {
            "name": "CBD(2)",
            "estimator": {"type": "centered_binomial", "eta": 2},
        }
        payload = {
            "problem": "ntru",
            "n": 512,
            "q": 12289,
            "secret_distribution": distribution,
            "error_distribution": distribution,
            "estimator_profile": "standard",
            "hard_problem_variant": "ring",
            "ntru_type": "circulant",
            "ring_degree": 512,
            "per_attack_timeout": 1,
        }
        with (
            patch.dict(sys.modules, {"estimator": estimator_module}),
            patch("app.estimator_runner.reduction_model_variants", return_value=models),
            patch("app.estimator_runner.cost_to_json", side_effect=fake_cost_to_json),
            patch("app.estimator_runner.estimator_commit", return_value="abc1234"),
        ):
            return run_ntru(payload)


class EstimatorSpaceAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.space_app = load_space_app()

    @staticmethod
    def payload(profile="standard"):
        variant = "rlwe" if profile == "enhanced" else "lwe"
        return {
            "problem": "lwe",
            "n": 512,
            "q": 12289,
            "distribution": {
                "estimator": {"type": "centered_binomial", "eta": 2},
            },
            "estimator_profile": profile,
            "hard_problem_variant": variant,
        }

    def test_worker_rejects_profile_variant_mismatch_before_subprocess(self):
        payload = self.payload("enhanced")
        payload["hard_problem_variant"] = "lwe"

        with self.assertRaises(EstimatorRouteError) as raised:
            self.space_app.validate_payload(payload)
        self.assertEqual(raised.exception.code, "invalid_estimator_route")

        with patch.object(self.space_app.subprocess, "run") as run:
            result = self.space_app.run_estimator_subprocess(payload, 5)
        self.assertEqual(result["code"], "invalid_estimator_route")
        self.assertIn("lwe/enhanced", result["message"])
        run.assert_not_called()

    def test_worker_rejects_missing_variant_without_defaulting_to_lwe(self):
        payload = self.payload("enhanced")
        del payload["hard_problem_variant"]

        with self.assertRaises(EstimatorRouteError) as raised:
            self.space_app.validate_payload(payload)
        self.assertEqual(raised.exception.code, "invalid_estimator_route")

        with patch.object(self.space_app.subprocess, "run") as run:
            result = self.space_app.run_estimator_subprocess(payload, 5)
        self.assertEqual(result["code"], "invalid_estimator_route")
        run.assert_not_called()

    def test_worker_rejects_contradictory_ntru_type_before_subprocess(self):
        payload = {
            "problem": "ntru",
            "n": 509,
            "q": 2048,
            "secret_distribution": {
                "estimator": {"type": "centered_binomial", "eta": 2},
            },
            "error_distribution": {
                "estimator": {"type": "centered_binomial", "eta": 2},
            },
            "estimator_profile": "standard",
            "hard_problem_variant": "ring",
            "ntru_type": "matrix",
        }

        with self.assertRaises(EstimatorRouteError) as raised:
            self.space_app.validate_payload(payload)
        self.assertEqual(raised.exception.code, "invalid_estimator_route")

        with patch.object(self.space_app.subprocess, "run") as run:
            result = self.space_app.run_estimator_subprocess(payload, 5)
        self.assertEqual(result["code"], "invalid_estimator_route")
        run.assert_not_called()

    def test_worker_serialization_replaces_lone_unicode_surrogates(self):
        handler = object.__new__(self.space_app.EstimatorHandler)
        handler.wfile = BytesIO()
        handler.send_response = lambda _status: None
        handler.write_cors_headers = lambda: None
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None

        handler.write_json({"valid": "中文", "bad\ud800": "value\udfff"})

        raw = handler.wfile.getvalue()
        decoded = raw.decode("utf-8", errors="strict")
        payload = json.loads(decoded)
        self.assertEqual(payload["valid"], "中文")
        self.assertEqual(payload["bad\\ud800"], "value\\udfff")

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

    def test_subprocess_rejects_invalid_selected_estimator_root(self):
        with TemporaryDirectory() as invalid:
            environment = {"ENHANCED_LATTICE_ESTIMATOR_PATH": invalid}
            with patch.dict(os.environ, environment, clear=True), patch.object(
                self.space_app.subprocess,
                "run",
            ) as run:
                result = self.space_app.run_estimator_subprocess(self.payload("enhanced"), 5)

        self.assertEqual(
            result,
            {
                "ok": False,
                "code": "estimator_path_invalid",
                "message": "enhanced estimator path does not contain estimator/__init__.py.",
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
        with TemporaryDirectory() as standard, TemporaryDirectory() as enhanced:
            for root in (standard, enhanced):
                package = Path(root) / "estimator"
                package.mkdir()
                (package / "__init__.py").write_text("", encoding="utf-8")
            paths = {
                "SAGE_BINARY": "/test/sage",
                "LATTICE_ESTIMATOR_PATH": standard,
                "ENHANCED_LATTICE_ESTIMATOR_PATH": str(Path(enhanced) / "estimator"),
                "PYTHONPATH": "/ambient/import/path",
            }

            with patch.dict(os.environ, paths), patch.object(
                self.space_app.subprocess,
                "run",
                return_value=completed,
            ) as run:
                for profile, expected in (("standard", standard), ("enhanced", enhanced)):
                    with self.subTest(profile=profile):
                        before = run.call_count
                        result = self.space_app.run_estimator_subprocess(self.payload(profile), 5)
                        self.assertTrue(result["ok"])
                        self.assertEqual(run.call_count, before + 2)
                        preflight_call, runner_call = run.call_args_list[-2:]
                        self.assertEqual(
                            preflight_call.args[0][0:3],
                            ["/test/sage", "-python", "-c"],
                        )
                        self.assertEqual(preflight_call.args[0][-2], expected)
                        self.assertEqual(
                            runner_call.args[0],
                            ["/test/sage", "-python", str(self.space_app.RUNNER)],
                        )
                        for call in (preflight_call, runner_call):
                            environment = call.kwargs["env"]
                            self.assertEqual(environment["PYTHONPATH"], expected)
                            self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
                            self.assertEqual(
                                environment["EASYLATTICE_ESTIMATOR_ROOT"],
                                expected,
                            )

    def test_origin_preflight_blocks_competing_estimator_in_actual_subprocess(self):
        with (
            TemporaryDirectory() as selected,
            TemporaryDirectory() as competing,
            TemporaryDirectory() as scripts,
        ):
            for root, marker in ((selected, "selected"), (competing, "competing")):
                package = Path(root) / "estimator"
                package.mkdir()
                (package / "__init__.py").write_text(
                    f"SOURCE = {marker!r}\n",
                    encoding="utf-8",
                )

            sentinel = Path(scripts) / "runner-executed"
            runner = Path(scripts) / "runner.py"
            runner.write_text(
                "from pathlib import Path\n"
                f"Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')\n"
                "print('{\"ok\": true}')\n",
                encoding="utf-8",
            )
            sage = Path(scripts) / "sage"
            sage.write_text(
                "#!/bin/sh\n"
                "shift\n"
                f"PYTHONPATH={shlex.quote(competing)} "
                f"exec {shlex.quote(sys.executable)} \"$@\"\n",
                encoding="utf-8",
            )
            sage.chmod(0o755)

            environment = {
                "SAGE_BINARY": str(sage),
                "ENHANCED_LATTICE_ESTIMATOR_PATH": selected,
                "PYTHONPATH": competing,
            }
            with patch.dict(os.environ, environment, clear=True), patch.object(
                self.space_app,
                "RUNNER",
                runner,
            ):
                result = self.space_app.run_estimator_subprocess(self.payload("enhanced"), 5)

            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "estimator_origin_mismatch")
            self.assertIn(str(Path(competing).resolve()), result["message"])
            self.assertIn(str(Path(selected).resolve()), result["message"])
            self.assertFalse(sentinel.exists())

    def test_remote_runner_preserves_post_preflight_origin_mismatch(self):
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
            environment = {
                "SAGE_BINARY": str(sage),
                "ENHANCED_LATTICE_ESTIMATOR_PATH": selected,
                "EASYLATTICE_ESTIMATOR_ROOT": competing,
                "PYTHONPATH": competing,
            }

            with patch.dict(os.environ, environment, clear=True):
                result = self.space_app.run_estimator_subprocess(
                    self.payload("enhanced"),
                    5,
                )
            reached_runner = runner_executed.exists()

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "estimator_origin_mismatch")
        self.assertIn(str(Path(competing).resolve()), result["message"])
        self.assertIn(str(Path(selected).resolve()), result["message"])
        self.assertTrue(reached_runner)

    @staticmethod
    def pinned_enhanced_commits():
        root = Path(__file__).resolve().parents[1]
        commits = []
        for relative_path in (
            "deploy/huggingface-estimator/Dockerfile",
            "deploy/huggingface-live/Dockerfile",
        ):
            contents = (root / relative_path).read_text(encoding="utf-8")
            required = (
                "remote add origin https://github.com/identitymapping/enhanced_lattice-estimator.git",
                "checkout --detach FETCH_HEAD",
                "ENV ENHANCED_LATTICE_ESTIMATOR_PATH=/opt/enhanced-lattice-estimator",
            )
            for expected in required:
                if expected not in contents:
                    raise AssertionError(f"missing {expected!r} in {relative_path}")
            match = re.search(r"fetch --depth=1 origin ([0-9a-f]{40})", contents)
            if not match:
                raise AssertionError(f"missing enhanced estimator commit pin in {relative_path}")
            commits.append(match.group(1))
        return commits

    def test_docker_images_pin_enhanced_estimator_profile(self):
        commits = self.pinned_enhanced_commits()

        self.assertEqual(commits, [commits[0], commits[0]])
        self.assertEqual(len(commits[0]), 40)

    @unittest.skipUnless(
        os.environ.get("EASYLATTICE_RUN_PINNED_ESTIMATOR_SMOKE") == "1",
        "set EASYLATTICE_RUN_PINNED_ESTIMATOR_SMOKE=1 to fetch the pinned estimator",
    )
    def test_pinned_enhanced_estimator_checkout_has_expected_package_origin(self):
        commit = self.pinned_enhanced_commits()[0]
        remote = "https://github.com/identitymapping/enhanced_lattice-estimator.git"
        with TemporaryDirectory() as directory:
            checkout = Path(directory) / "enhanced-lattice-estimator"
            commands = (
                ["git", "init", str(checkout)],
                ["git", "-C", str(checkout), "remote", "add", "origin", remote],
                ["git", "-C", str(checkout), "fetch", "--depth=1", "origin", commit],
                ["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"],
            )
            for command in commands:
                subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    timeout=120,
                    check=True,
                )

            self.assertTrue((checkout / "estimator" / "__init__.py").is_file())
            self.assertTrue((checkout / ".git" / "shallow").is_file())
            revision = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                timeout=5,
                check=True,
            ).stdout.strip()
            self.assertEqual(revision, commit)

            origin_check = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import importlib.util, sys; "
                        "from pathlib import Path; "
                        "spec = importlib.util.find_spec('estimator'); "
                        "origin = Path(spec.origin).resolve(); "
                        "print(origin.parent.parent); "
                        "sys.exit(0 if origin.parent.parent == Path(sys.argv[1]).resolve() else 1)"
                    ),
                    str(checkout),
                ],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
                env={"PYTHONPATH": str(checkout), "PYTHONNOUSERSITE": "1"},
            )
            self.assertEqual(origin_check.returncode, 0, origin_check.stderr)


if __name__ == "__main__":
    unittest.main()
