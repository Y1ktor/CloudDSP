"""Commit one already-classified Demucs source-failure decision briefly.

``pre_model_failure_transition.py`` owns the parameterized lease-token-guarded
SQL. This module adds only the short commit-or-rollback scope around it. A
caller supplies the exact durable lease and finite classification that it
already obtained from the source-preflight path; this function exposes a retry
or terminal result only after normal ``write_cursor()`` exit commits it.

The boundary stays deliberately transport- and runtime-neutral. It does not
catch/classify the original exception, inspect MinIO/FFprobe/Demucs, receive or
acknowledge RabbitMQ, sleep for the retry delay, recover a task, renew a lease,
open a database connection, build an image, or act on Kubernetes. The next
composition may catch a reviewed source exception and delegate here without
holding any transaction open across object storage or CPU work.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from app.pre_model_failure_transition import (
    DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    DemucsPreModelFailureTransition,
    transition_leased_demucs_pre_model_failure,
)
from app.source_failure_classification import DemucsPreModelFailureClassification
from app.task_lease import DatabaseCursor, DemucsTaskLease


class DemucsPreModelFailureTransitionDatabase(Protocol):
    """The one database capability required for this short durable decision."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a cursor that commits normally and rolls back on exception."""


def commit_leased_demucs_pre_model_failure_transition(
    *,
    database: DemucsPreModelFailureTransitionDatabase,
    lease: DemucsTaskLease,
    classification: DemucsPreModelFailureClassification,
    retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
) -> DemucsPreModelFailureTransition | None:
    """Commit one pre-model retry/terminal decision, or return ownership loss.

    ``None`` means the underlying guarded statement found no current eligible
    row. The lease may have expired, a recovery worker may now own it, or its
    Job/task state may have changed. This normal result is exposed only after
    the database context exits, so a caller must stop rather than construct a
    competing retry or terminal outcome.

    Any database outage, invalid returned row, or programming failure escapes
    through ``write_cursor()`` and therefore rolls back. It cannot masquerade
    as a committed retry/terminal decision. This function creates no database
    connection itself; the restricted Psycopg adapter supplies the context.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    # This scope contains exactly the one guarded task/Job update. It ends
    # before any caller can sleep, recover work, start a model, or touch AMQP;
    # a normal return therefore proves PostgreSQL accepted the durable result.
    with database.write_cursor() as cursor:
        transition = transition_leased_demucs_pre_model_failure(
            cursor,
            lease=lease,
            classification=classification,
            retry_after_seconds=retry_after_seconds,
        )
    return transition
