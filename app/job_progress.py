from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    estimator_profile: str | None = None
    estimator_commit: str | None = None
    # The task fields are deliberately optional so callers that only know
    # about the coarse job stages remain source compatible.  ``candidate``
    # is intentionally untyped: search layers commonly use either a
    # candidate dictionary or a short human-readable label.
    candidate: Any = None
    model: str | None = None
    mode: str | None = None
    attack: str | None = None
    completed: int | None = None
    total: int | None = None
    cancelled: bool = False


Reporter = Callable[[ProgressEvent], None]
_REPORTER: ContextVar[Reporter | None] = ContextVar(
    "easyLattice_job_reporter",
    default=None,
)


@contextmanager
def progress_reporting(reporter: Reporter) -> Iterator[None]:
    token = _REPORTER.set(reporter)
    try:
        yield
    finally:
        _REPORTER.reset(token)


def report_progress(
    stage: str,
    estimator_profile: str | None = None,
    estimator_commit: str | None = None,
    *,
    candidate: Any = None,
    model: str | None = None,
    mode: str | None = None,
    attack: str | None = None,
    completed: int | None = None,
    total: int | None = None,
    cancelled: bool = False,
) -> None:
    reporter = _REPORTER.get()
    if reporter is not None:
        reporter(
            ProgressEvent(
                stage,
                estimator_profile,
                estimator_commit,
                candidate,
                model,
                mode,
                attack,
                completed,
                total,
                cancelled,
            )
        )
