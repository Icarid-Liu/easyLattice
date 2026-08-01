"""Small, cancellable units of estimator work.

The search code submits one task for a single reduction model, security mode,
and attack.  Keeping this contract separate from the Sage runner makes it
possible for the local job server to cancel a running task without changing
the legacy request that evaluates all four model/mode combinations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Event
from typing import Any

from .config import AppConfig, EstimatorConfig
from .estimator_process import run_estimator
from .job_progress import report_progress


LWE_TASK_ATTACKS = frozenset({"usvp", "dual_hybrid", "bdd_hybrid"})
NTRU_TASK_ATTACKS = frozenset(
    {"usvp", "dsd", "bdd", "bdd_hybrid", "bdd_mitm_hybrid"}
)
TASK_MODELS = frozenset({"matzov", "adps16"})
TASK_MODES = frozenset({"classical", "quantum"})


@dataclass(frozen=True)
class EstimatorTask:
    """One estimator unit, identified by model, mode, and attack name."""

    model: str
    mode: str
    attack: str

    def __post_init__(self) -> None:
        if self.model not in TASK_MODELS:
            raise ValueError("estimator task model must be matzov or adps16.")
        if self.mode not in TASK_MODES:
            raise ValueError("estimator task mode must be classical or quantum.")
        if not self.attack:
            raise ValueError("estimator task attack is required.")

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _app_config(config: AppConfig | EstimatorConfig) -> AppConfig:
    if isinstance(config, AppConfig):
        return config
    if isinstance(config, EstimatorConfig):
        return AppConfig(estimator=config)
    # Duck-typing is useful to callers that provide a small test config while
    # keeping a clear error for accidental positional arguments.
    estimator = getattr(config, "estimator", None)
    if estimator is not None:
        return config  # type: ignore[return-value]
    raise TypeError("config must be AppConfig or EstimatorConfig.")


def _task_attacks(payload: dict[str, Any]) -> frozenset[str]:
    problem = str(payload.get("problem", "lwe")).lower()
    return NTRU_TASK_ATTACKS if problem == "ntru" else LWE_TASK_ATTACKS


def run_estimator_task(
    payload: dict[str, Any],
    task: EstimatorTask,
    config: AppConfig | EstimatorConfig,
    profile: str,
    cancel_event: Event | None = None,
) -> dict[str, Any]:
    """Run one model/mode/attack and emit task-level progress.

    ``run_estimator`` still accepts the historical full-estimate payload.  A
    task simply adds the three optional routing fields; the Sage runner then
    evaluates that one slice and returns the same model/mode summary shape.
    A cancellation that is already requested is handled before estimator
    preparation, which is important for queued tasks and makes cancellation
    deterministic in tests.
    """

    allowed = _task_attacks(payload)
    if task.attack not in allowed:
        raise ValueError(
            f"Unsupported {payload.get('problem', 'lwe').lower()} estimator attack: {task.attack}"
        )

    candidate = payload.get("candidate")
    if candidate is None and "n" in payload:
        candidate = {"n": payload.get("n"), "q": payload.get("q")}
    total = 1
    if cancel_event is not None and cancel_event.is_set():
        event = {
            "candidate": candidate,
            "model": task.model,
            "mode": task.mode,
            "attack": task.attack,
            "completed": 0,
            "total": total,
            "cancelled": True,
        }
        report_progress(
            "estimator_running",
            profile,
            None,
            **event,
        )
        report_progress("estimator_cancelled", profile, None, **event)
        return {
            "ok": False,
            "code": "attack_cancelled",
            "message": "Estimator attack was cancelled before it started.",
            "cancelled": True,
            "task": task.as_dict(),
        }

    normalized = dict(payload)
    normalized.update(task.as_dict())
    report_progress(
        "estimator_running",
        profile,
        None,
        candidate=candidate,
        model=task.model,
        mode=task.mode,
        attack=task.attack,
        completed=0,
        total=total,
    )
    result = run_estimator(
        normalized,
        timeout=None,
        config=_app_config(config),
        profile=profile,
        cancel_event=cancel_event,
    )
    cancelled = bool(
        result.get("cancelled")
        or result.get("code") == "attack_cancelled"
        or (cancel_event is not None and cancel_event.is_set())
    )
    report_progress(
        "estimator_cancelled" if cancelled else "estimator_attack_completed",
        profile,
        result.get("estimator_commit"),
        candidate=candidate,
        model=task.model,
        mode=task.mode,
        attack=task.attack,
        completed=0 if cancelled else 1,
        total=total,
        cancelled=cancelled,
    )
    if isinstance(result, dict):
        result.setdefault("task", task.as_dict())
    return result


__all__ = ["EstimatorTask", "run_estimator_task"]
