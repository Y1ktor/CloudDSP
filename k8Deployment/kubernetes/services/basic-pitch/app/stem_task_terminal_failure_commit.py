"""Commit one Basic Pitch pre-model terminal task failure in a short transaction.

``stem_task_terminal_failure.py`` owns the pure parameterized SQL decision.
This module supplies only the missing commit-or-rollback scope around that
decision.  It is intentionally separate from exception classification, MinIO,
RabbitMQ, and the model coordinator: a caller must already have a reviewed
permanent failure code and the exact lease that observed it.

No PostgreSQL connection is created here.  The existing restricted database
adapter supplies ``write_cursor()``; normal context exit commits, and an
exceptional exit rolls back.  The function returns success evidence only after
that normal exit, so a later worker runtime cannot claim an input mismatch was
durably recorded before PostgreSQL has accepted it.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from app.stem_task_terminal_failure import (
    BasicPitchStemTerminalFailure,
    BasicPitchStemTerminalFailureCode,
    fail_leased_basic_pitch_stem_task,
)
from app.task_lease import BasicPitchTaskLease, DatabaseCursor


class BasicPitchStemTerminalFailureDatabase(Protocol):
    """The only database capability used for one terminal preflight result."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a short cursor that commits normally and rolls back on errors."""


def commit_terminal_basic_pitch_stem_failure(
    *,
    database: BasicPitchStemTerminalFailureDatabase,
    lease: BasicPitchTaskLease,
    failure_code: BasicPitchStemTerminalFailureCode,
) -> BasicPitchStemTerminalFailure | None:
    """Commit one guarded terminal failure, or return normal ownership loss.

    ``None`` means the pure statement found no current matching ``leased``
    task.  The worker may have lost its lease, another replica may have
    recovered it, or a later state transition may already have won; callers
    must stop rather than create another result.  This function does not
    inspect an exception, retry, log, touch RabbitMQ, or update the overall
    Job.  Those responsibilities remain outside this transaction boundary.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    with database.write_cursor() as cursor:
        terminal_failure = fail_leased_basic_pitch_stem_task(
            cursor,
            lease=lease,
            failure_code=failure_code,
        )
    return terminal_failure
