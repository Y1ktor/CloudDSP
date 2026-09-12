"""Commit one Basic Pitch transient pre-model retry schedule briefly.

``stem_task_retry_schedule.py`` owns the pure parameterized SQL decision. This
module supplies only its short commit-or-rollback scope. It remains separate
from exception classification, attempt-exhaustion policy, MinIO, RabbitMQ, and
the model coordinator: a caller must already have a reviewed transient failure
code and the exact lease that observed it.

No PostgreSQL connection is created here. The existing restricted database
adapter supplies ``write_cursor()``; normal context exit commits, and an
exceptional exit rolls back. The function returns retry evidence only after
that normal exit, so a later worker runtime cannot arrange another delivery
before PostgreSQL durably records its ``retry_scheduled`` state and time.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from app.stem_task_retry_schedule import (
    DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS,
    BasicPitchStemRetrySchedule,
    BasicPitchStemRetryScheduleCode,
    schedule_leased_basic_pitch_stem_retry,
)
from app.task_lease import BasicPitchTaskLease, DatabaseCursor


class BasicPitchStemRetryScheduleDatabase(Protocol):
    """The only database capability used for one transient preflight result."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a short cursor that commits normally and rolls back on errors."""


def commit_basic_pitch_stem_retry_schedule(
    *,
    database: BasicPitchStemRetryScheduleDatabase,
    lease: BasicPitchTaskLease,
    retry_after_seconds: int = DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS,
    failure_code: BasicPitchStemRetryScheduleCode,
) -> BasicPitchStemRetrySchedule | None:
    """Commit one guarded retry schedule, or return normal no-schedule evidence.

    ``None`` means the pure statement did not find a current retryable
    ``leased`` task. The worker may have lost its lease, another replica may
    have recovered it, a later state transition may already have won, or the
    final allowed attempt may have been reached. Callers must stop rather than
    manufacture a second outcome. A later policy owns the explicit exhaustion
    result and any RabbitMQ re-delivery arrangement.

    This function does not inspect an exception, choose retryability, sleep,
    touch MinIO/RabbitMQ, run Basic Pitch, or update the overall Job. Those
    responsibilities remain outside this transaction boundary.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    with database.write_cursor() as cursor:
        retry_schedule = schedule_leased_basic_pitch_stem_retry(
            cursor,
            lease=lease,
            retry_after_seconds=retry_after_seconds,
            failure_code=failure_code,
        )
    return retry_schedule
