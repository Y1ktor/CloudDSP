"""Commit one Basic Pitch final-attempt storage-retry exhaustion briefly.

``stem_task_retry_exhaustion.py`` owns the pure parameterized terminal SQL
decision. This module supplies only its short commit-or-rollback scope. It is
separate from exception classification, MinIO/RabbitMQ, retry/recovery timing,
and the model coordinator: a caller must already have the exact final lease and
the reviewed storage-retry-exhausted code.

No PostgreSQL connection is created here. The existing restricted database
adapter supplies ``write_cursor()``; normal context exit commits, and an
exceptional exit rolls back. The function returns terminal evidence only after
normal exit, so a later worker cannot report that the retry budget was spent
until PostgreSQL accepts the durable ``failed`` task state.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from app.stem_task_retry_exhaustion import (
    BasicPitchStemRetryExhaustion,
    BasicPitchStemRetryExhaustionCode,
    fail_final_attempt_leased_basic_pitch_stem,
)
from app.task_lease import BasicPitchTaskLease, DatabaseCursor


class BasicPitchStemRetryExhaustionDatabase(Protocol):
    """The only database capability used for one final storage-retry result."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a short cursor that commits normally and rolls back on errors."""


def commit_final_attempt_basic_pitch_stem_retry_exhaustion(
    *,
    database: BasicPitchStemRetryExhaustionDatabase,
    lease: BasicPitchTaskLease,
    failure_code: BasicPitchStemRetryExhaustionCode,
) -> BasicPitchStemRetryExhaustion | None:
    """Commit one guarded final storage-retry failure, or return ownership loss.

    ``None`` means the pure statement found no current matching final
    ``leased`` task. The worker may have lost the lease, another replica may
    have recovered it, expiry may have occurred, or another state transition
    may already have won. Callers must stop rather than create another terminal
    result. This function does not inspect an exception, classify retryability,
    retry, log, touch MinIO/RabbitMQ, or update the aggregate Job.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    with database.write_cursor() as cursor:
        exhaustion = fail_final_attempt_leased_basic_pitch_stem(
            cursor,
            lease=lease,
            failure_code=failure_code,
        )
    return exhaustion
