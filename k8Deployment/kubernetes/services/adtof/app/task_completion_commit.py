"""Commit one verified ADTOF drums-task result in a short transaction.

The preceding boundaries already validated the local tempo observation, proved
MinIO stored both deterministic outputs, and constructed the typed guarded
completion call. This composition supplies only the transaction lifetime: it
opens the existing restricted ``write_cursor()`` context, calls the completion
adapter, and returns a success value only after normal context exit commits.

It does not create a database connection, apply the bootstrap Job, access
MinIO, receive/acknowledge RabbitMQ, invoke ADTOF, classify retries, or use the
Kubernetes API. A later worker supervisor decides broker acknowledgement only
after this function returns committed completion evidence.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from app.output_artifact import ADTOFTempoCandidate
from app.output_artifact_head_object import VerifiedStoredADTOFObjects
from app.task_claim import ADTOFTaskLease, DatabaseCursor
from app.task_completion import ADTOFTaskCompletion, complete_running_adtof_task


class ADTOFTaskCompletionDatabase(Protocol):
    """The one database capability needed for one final ADTOF state write."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a short cursor that commits normally and rolls back on error."""


def commit_verified_adtof_task(
    *,
    database: ADTOFTaskCompletionDatabase,
    lease: ADTOFTaskLease,
    stored_outputs: VerifiedStoredADTOFObjects,
    tempo_candidate: ADTOFTempoCandidate,
) -> ADTOFTaskCompletion | None:
    """Commit a guarded ADTOF success, or return normal stale-ownership loss.

    A non-``None`` completion remains inside the context until the cursor's
    normal exit commits it; callers never see a successful result from an
    uncommitted transaction. ``None`` is the guarded function's no-row outcome
    for lease recovery/expiry, Job transition/deletion, retention expiry, or a
    prior drum result, and exits normally because it changed nothing. Any SQL
    result-validation or database failure leaves the context exceptionally, so
    the database implementation rolls back before the caller may decide retry,
    terminal failure, or AMQP acknowledgement.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    # Keep this lock-bearing scope minimal. Upload/HeadObject/model work was
    # completed before entry, and RabbitMQ acknowledgement follows its commit.
    with database.write_cursor() as cursor:
        completion = complete_running_adtof_task(
            cursor,
            lease=lease,
            stored_outputs=stored_outputs,
            tempo_candidate=tempo_candidate,
        )
    return completion
