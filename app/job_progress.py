from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    estimator_profile: str | None = None
    estimator_commit: str | None = None


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
) -> None:
    reporter = _REPORTER.get()
    if reporter is not None:
        reporter(ProgressEvent(stage, estimator_profile, estimator_commit))
