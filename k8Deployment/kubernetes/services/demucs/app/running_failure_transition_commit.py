"""Commit one reviewed Demucs ``running``-phase failure decision briefly.

``running_failure_transition.py`` owns the parameterized token-guarded SQL
that schedules a post-model retry or records final retry exhaustion. This
module supplies only the short commit-or-rollback scope around that statement.
It exposes a result after normal context exit, so no caller can report a retry
time or terminal Job failure before PostgreSQL has accepted it.

It does not catch or classify a runtime exception, open a database connection,
contact MinIO/RabbitMQ, sleep, recover/renew a lease, execute Demucs, build an
image, or change a Deployment/KEDA resource. The separate runtime handoff
decides which already-running exceptions may invoke this committed transition.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from app.running_failure_classification import DemucsRunningFailureClassification
from app.running_failure_transition import (
    DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
    DemucsRunningFailureTransition,
    transition_running_demucs_failure,
)
from app.task_lease import DatabaseCursor, DemucsTaskLease


class DemucsRunningFailureTransitionDatabase(Protocol):
    """The only database capability required to commit one running result."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a short cursor that commits normally and rolls back on error."""


def commit_running_demucs_failure_transition(
    *,
    database: DemucsRunningFailureTransitionDatabase,
    lease: DemucsTaskLease,
    classification: DemucsRunningFailureClassification,
    retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
) -> DemucsRunningFailureTransition | None:
    """Commit a running retry/exhaustion decision, or return ownership loss.

    ``None`` is a normal committed no-row result: the lease may have expired,
    another worker may have recovered it, a result may have already won, or the
    Job may no longer be retained/processable. The caller must stop instead of
    writing another task outcome. An SQL/protocol/database exception escapes
    through the context manager so it rolls back before any caller sees a
    seemingly durable retry or failure result.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    # This transaction contains only the one state transition. It ends before
    # a future supervisor receives another delivery, sleeps for retry timing,
    # starts CPU work, or reaches MinIO/RabbitMQ.
    with database.write_cursor() as cursor:
        transition = transition_running_demucs_failure(
            cursor,
            lease=lease,
            classification=classification,
            retry_after_seconds=retry_after_seconds,
        )
    return transition
