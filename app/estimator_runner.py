from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path


class AttackTimeout(Exception):
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

    if value == oo:
        return None
    return float(log(value, 2))


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


def run(payload: dict) -> dict:
    from estimator import LWE, ND
    from estimator.reduction import ADPS16

    n = int(payload["n"])
    q = int(payload["q"])
    distribution = payload["distribution"]
    per_attack_timeout = int(payload.get("per_attack_timeout", 8))
    X = estimator_distribution(ND, distribution, n)
    params = LWE.Parameters(
        n=n,
        q=q,
        Xs=X,
        Xe=X,
        m=n,
        tag=f"RLWE screen n={n}, q={q}, {distribution.get('name', distribution.get('family'))}",
    ).normalize()

    modes = {}
    for mode in ("classical", "quantum"):
        model = ADPS16(mode=mode)
        attacks = {}
        for name in ("usvp", "dual_hybrid"):
            try:
                with time_limit(per_attack_timeout):
                    if name == "usvp":
                        cost = LWE.primal_usvp(params, red_cost_model=model, red_shape_model="gsa")
                    else:
                        cost = LWE.dual_hybrid(params, red_cost_model=model)
                attacks[name] = {"ok": True, **cost_to_json(cost)}
            except AttackTimeout as exc:
                attacks[name] = {"ok": False, "message": str(exc)}
            except Exception as exc:
                attacks[name] = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}

        successful = {
            name: result
            for name, result in attacks.items()
            if result.get("ok") and result.get("rop_bits") is not None
        }
        if successful:
            best_attack, best_result = min(successful.items(), key=lambda item: item[1]["rop_bits"])
            modes[mode] = {
                "ok": True,
                "min_bits": best_result["rop_bits"],
                "best_attack": best_attack,
                "attacks": attacks,
            }
        else:
            modes[mode] = {
                "ok": False,
                "message": "no attack estimate completed",
                "attacks": attacks,
            }

    ok = any(mode.get("ok") for mode in modes.values())
    return {
        "ok": ok,
        "estimator_commit": estimator_commit(),
        "modes": modes,
        "parameters": {"n": n, "q": q, "distribution": distribution, "m": n},
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
    raise ValueError(f"Unsupported estimator distribution: {estimator_type}")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        print(json.dumps(run(payload), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "message": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
