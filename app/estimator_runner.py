from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.compression_noise import compression_noise_estimator_distribution
from app.estimator_contract import (
    EstimatorRouteError,
    LWE_ATTACKS,
    structure_correction_metadata,
    structure_correction_satisfied,
    validate_estimator_route,
)


NTRU_ATTACKS = ("usvp", "dsd", "bdd", "bdd_hybrid", "bdd_mitm_hybrid")


class AttackTimeout(Exception):
    pass


class EstimatorOriginMismatch(Exception):
    pass


@contextmanager
def time_limit(seconds: int):
    import signal

    def handler(_signum, _frame):
        raise AttackTimeout(f"attack exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def estimator_commit() -> str | None:
    try:
        import estimator

        root = Path(estimator.__file__).resolve().parents[1]
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()
    except Exception:
        return None
    return None


def log2_or_none(value):
    from sage.all import log, oo

    if value is None or value == oo:
        return None
    try:
        result = float(log(value, 2))
    except Exception:
        return None
    return result if math.isfinite(result) else None


def cost_to_json(cost) -> dict:
    fields = {}
    for key in ("rop", "red", "guess"):
        if key in cost:
            fields[f"{key}_bits"] = log2_or_none(cost[key])
    for key in ("β", "beta", "β'", "d", "p", "ζ", "t", "m"):
        if key in cost:
            try:
                fields[str(key)] = int(cost[key])
            except Exception:
                fields[str(key)] = str(cost[key])
    for key in ("δ", "delta"):
        if key in cost:
            try:
                fields["delta"] = float(cost[key])
            except Exception:
                fields["delta"] = str(cost[key])
    fields["summary"] = repr(cost)
    return fields


def finite_rop_bits(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def valid_raw_rop(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        if value <= 0:
            return False
    except (TypeError, ValueError, ArithmeticError):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return True
    except (TypeError, ValueError):
        return False


def attack_result(cost) -> dict:
    try:
        raw_rop = cost["rop"]
    except (KeyError, TypeError):
        raw_rop = None
    if not valid_raw_rop(raw_rop):
        return {
            "ok": False,
            "code": "invalid_attack_cost",
            "message": "attack estimate returned no finite rop",
            "summary": repr(cost),
        }
    fields = cost_to_json(cost)
    if not finite_rop_bits(fields.get("rop_bits")):
        return {
            "ok": False,
            "code": "invalid_attack_cost",
            "message": "attack estimate returned no finite rop",
            "summary": fields.get("summary", repr(cost)),
        }
    return {"ok": True, **fields}


def verify_estimator_origin() -> None:
    configured_root = os.environ.get("EASYLATTICE_ESTIMATOR_ROOT")
    if not configured_root:
        return
    expected_root = Path(configured_root).expanduser().resolve()
    try:
        import estimator

        origin = Path(estimator.__file__).resolve()
        actual_root = origin.parent.parent if origin.parent.name == "estimator" else origin.parent
        actual_root = actual_root.resolve()
    except Exception as exc:
        raise EstimatorOriginMismatch(
            f"Could not import the selected estimator: {type(exc).__name__}: {exc}"
        ) from exc
    if actual_root != expected_root:
        raise EstimatorOriginMismatch(
            f"Estimator imported from {actual_root}, expected {expected_root}."
        )


def reduction_model_variants() -> dict[str, dict[str, object]]:
    from estimator.reduction import ADPS16, MATZOV

    return {
        "matzov": {
            "classical": MATZOV(),
            "quantum": MATZOV(nn="quantum"),
        },
        "adps16": {
            "classical": ADPS16(),
            "quantum": ADPS16(mode="quantum"),
        },
    }


def summarize_attacks(attacks: dict[str, dict]) -> dict:
    successful = {
        name: result
        for name, result in attacks.items()
        if result.get("ok") and finite_rop_bits(result.get("rop_bits"))
    }
    covered = {
        name: result
        for name, result in successful.items()
        if structure_correction_satisfied(result)
    }
    if not covered:
        return {
            "ok": False,
            "complete": False,
            "message": "no covered attack estimate completed",
            "attacks": attacks,
        }
    best_attack, best_result = min(covered.items(), key=lambda item: item[1]["rop_bits"])
    return {
        "ok": True,
        "complete": (
            len(successful) == len(attacks)
            and all(structure_correction_satisfied(result) for result in attacks.values())
        ),
        "min_bits": best_result["rop_bits"],
        "best_attack": best_attack,
        "attacks": attacks,
    }


def failure_mode(message: str) -> dict:
    return {"ok": False, "complete": False, "message": message, "attacks": {}}


def run_lwe_attack(LWE, params, name: str, model, mode: str, profile: str, ring_degree: int):
    if name == "usvp":
        return LWE.primal_usvp(params, red_cost_model=model, red_shape_model="gsa")
    if name == "dual_hybrid":
        return LWE.dual_hybrid(params, red_cost_model=model)
    if name == "bdd_hybrid":
        kwargs = {"red_cost_model": model, "mitm": False, "babai": False}
        if profile == "enhanced":
            kwargs.update({"deg_ring": ring_degree, "structure_leverage": True})
            if mode == "quantum":
                kwargs["Grover"] = True
        return LWE.primal_hybrid(params, **kwargs)
    raise ValueError(f"Unsupported LWE attack: {name}")


def run(payload: dict) -> dict:
    problem, _, _ = validate_estimator_route(
        payload.get("problem"),
        payload.get("estimator_profile"),
        payload.get("hard_problem_variant"),
    )
    verify_estimator_origin()
    if problem == "ntru":
        return run_ntru(payload)
    return run_lwe(payload)


def run_lwe(payload: dict) -> dict:
    problem, estimator_profile, hard_problem_variant = validate_estimator_route(
        payload.get("problem"),
        payload.get("estimator_profile"),
        payload.get("hard_problem_variant"),
    )
    if problem != "lwe":
        raise EstimatorRouteError(
            "invalid_estimator_route",
            "run_lwe requires problem=lwe.",
        )

    from estimator import LWE, ND

    n = int(payload["n"])
    q = int(payload["q"])
    ring_degree = int(payload.get("ring_degree", n))
    distribution = payload["distribution"]
    per_attack_timeout = int(payload.get("per_attack_timeout", 8))
    secret_distribution = payload.get("secret_distribution", distribution)
    error_distribution = payload.get("error_distribution", distribution)
    Xs = estimator_distribution(ND, secret_distribution, n)
    Xe = estimator_distribution(ND, error_distribution, n)
    params = LWE.Parameters(
        n=n,
        q=q,
        Xs=Xs,
        Xe=Xe,
        m=n,
        tag=f"RLWE screen n={n}, q={q}, {distribution.get('name', distribution.get('family'))}",
    ).normalize()

    models = {}
    for model_name, modes in reduction_model_variants().items():
        models[model_name] = {}
        for mode, model in modes.items():
            attacks = {}
            for name in LWE_ATTACKS:
                correction = structure_correction_metadata(
                    name,
                    estimator_profile,
                    hard_problem_variant,
                )
                try:
                    with time_limit(per_attack_timeout):
                        cost = run_lwe_attack(
                            LWE,
                            params,
                            name,
                            model,
                            mode,
                            estimator_profile,
                            ring_degree,
                        )
                    attacks[name] = attack_result(cost)
                except AttackTimeout as exc:
                    attacks[name] = {"ok": False, "message": str(exc)}
                except Exception as exc:
                    attacks[name] = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
                attacks[name]["structure_correction"] = correction
            models[model_name][mode] = summarize_attacks(attacks)

    default_modes = models["adps16"]
    ok = all(mode.get("ok") for family in models.values() for mode in family.values())
    complete = all(
        mode.get("complete") for family in models.values() for mode in family.values()
    )
    return {
        "ok": ok,
        "complete": complete,
        "estimator_profile": estimator_profile,
        "estimator_commit": estimator_commit(),
        "hard_problem_variant": hard_problem_variant,
        "ring_degree": ring_degree,
        "modes": default_modes,
        "models": models,
        "parameters": {
            "n": n,
            "q": q,
            "distribution": distribution,
            "secret_distribution": secret_distribution,
            "error_distribution": error_distribution,
            "m": n,
            "estimator_profile": estimator_profile,
            "hard_problem_variant": hard_problem_variant,
            "ring_degree": ring_degree,
        },
    }


def run_ntru(payload: dict) -> dict:
    problem, estimator_profile, hard_problem_variant = validate_estimator_route(
        payload.get("problem"),
        payload.get("estimator_profile"),
        payload.get("hard_problem_variant"),
    )
    if problem != "ntru":
        raise EstimatorRouteError(
            "invalid_estimator_route",
            "run_ntru requires problem=ntru.",
        )

    from estimator import NTRU, ND

    n = int(payload["n"])
    q = int(payload["q"])
    ring_degree = int(payload.get("ring_degree", n))
    per_attack_timeout = int(payload.get("per_attack_timeout", 20))
    Xs = estimator_distribution(ND, payload["secret_distribution"], n)
    Xe = estimator_distribution(ND, payload["error_distribution"], n)
    params = NTRU.Parameters(
        n=n,
        q=q,
        Xs=Xs,
        Xe=Xe,
        m=n,
        ntru_type=str(payload.get("ntru_type", "circulant")),
        tag=f"NTRU screen n={n}, q={q}",
    ).normalize()

    models = {}
    for model_name, modes in reduction_model_variants().items():
        models[model_name] = {}
        for mode, model in modes.items():
            try:
                with time_limit(per_attack_timeout):
                    estimates = NTRU.estimate(
                        params,
                        red_cost_model=model,
                        red_shape_model="zgsa",
                        quiet=True,
                        catch_exceptions=True,
                    )
                attacks = {}
                for name, cost in estimates.items():
                    attacks[name] = attack_result(cost)
                for name in NTRU_ATTACKS:
                    attacks.setdefault(
                        name,
                        {
                            "ok": False,
                            "code": "attack_not_returned",
                            "message": "estimator omitted attack result",
                        },
                    )
                models[model_name][mode] = summarize_attacks(attacks)
            except AttackTimeout as exc:
                models[model_name][mode] = failure_mode(str(exc))
            except Exception as exc:
                models[model_name][mode] = failure_mode(f"{type(exc).__name__}: {exc}")

    default_modes = models["adps16"]
    ok = all(mode.get("ok") for family in models.values() for mode in family.values())
    complete = all(
        mode.get("complete") for family in models.values() for mode in family.values()
    )
    return {
        "ok": ok,
        "complete": complete,
        "estimator_profile": estimator_profile,
        "estimator_commit": estimator_commit(),
        "hard_problem_variant": hard_problem_variant,
        "ring_degree": ring_degree,
        "modes": default_modes,
        "models": models,
        "parameters": {
            "n": n,
            "q": q,
            "m": n,
            "ntru_type": str(payload.get("ntru_type", "circulant")),
            "estimator_profile": estimator_profile,
            "hard_problem_variant": hard_problem_variant,
            "ring_degree": ring_degree,
        },
    }


def estimator_distribution(ND, distribution: dict, n: int):
    estimator = distribution.get("estimator", {})
    estimator_type = estimator.get("type")
    if estimator_type == "centered_binomial":
        return ND.CenteredBinomial(int(estimator["eta"]))
    if estimator_type == "sparse_ternary_fixed_weight":
        return ND.SparseTernary(
            int(estimator["plus_weight"]),
            int(estimator["minus_weight"]),
            n,
        )
    if estimator_type == "discrete_gaussian":
        return ND.DiscreteGaussian(float(estimator["stddev"]))
    if estimator_type == "uniform":
        return ND.Uniform(int(estimator["lower_bound"]), int(estimator["upper_bound"]))
    if estimator_type == "uniform_mod":
        return ND.UniformMod(int(estimator["modulus"]))
    if estimator_type == "compression_noise":
        return compression_noise_estimator_distribution(ND, estimator, n)
    if estimator_type == "composite_moment":
        bounds = estimator.get("bounds", [-10, 10])
        return ND.NoiseDistribution(
            n=n,
            mean=0,
            stddev=float(estimator["stddev"]),
            bounds=(int(bounds[0]), int(bounds[1])),
            _density=1.0,
        )
    raise ValueError(f"Unsupported estimator distribution: {estimator_type}")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        print(json.dumps(run(payload), ensure_ascii=False, allow_nan=False))
        return 0
    except EstimatorRouteError as exc:
        print(json.dumps(exc.as_result(), ensure_ascii=False, allow_nan=False))
        return 1
    except EstimatorOriginMismatch as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "estimator_origin_mismatch",
                    "message": str(exc),
                },
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "message": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
