"""Commit one ADTOF first-claim decision before any broker acknowledgement.

``task_claim.py`` deliberately contains the pure parameterized PostgreSQL
decision only. This small composition supplies the caller-owned transaction
scope: it creates the canonical drums lease or classifies a duplicate/stale
delivery, then exits the write context before returning the result. A concrete
future Psycopg adapter will implement that context with the restricted ADTOF
database Secret and a short commit-or-rollback transaction.

This module does not import Pika, receive/acknowledge/reject/retry a RabbitMQ
delivery, access MinIO, invoke ADTOF, update output state, sleep, or use the
Kubernetes API. If a Pod stops after commit but before a later acknowledgement,
RabbitMQ redelivery reaches the durable duplicate branch rather than creating
another `(job_id, 'adtof', 'drums')` task.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID, uuid4

from app.adtof_requested_message import ADTOFRequestedMessage
from app.task_claim import (
    DEFAULT_ADTOF_LEASE_SECONDS,
    DatabaseCursor,
    ADTOFTaskClaimResult,
    claim_adtof_task_for_delivery,
)


class ADTOFTaskClaimDatabase(Protocol):
    """The one database capability required for one ADTOF first-claim attempt.

    The structural protocol lets this small composition remain independent of
    Psycopg while its in-memory tests prove commit/rollback ordering. A future
    concrete adapter must yield a dictionary-row cursor and commit on normal
    exit or roll back when the pure claim function raises.
    """

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield one short commit-or-rollback transaction cursor."""


def claim_first_adtof_task(
    *,
    database: ADTOFTaskClaimDatabase,
    message: ADTOFRequestedMessage,
    lease_seconds: int = DEFAULT_ADTOF_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> ADTOFTaskClaimResult:
    """Return one committed claim/duplicate/stale decision for an ADTOF request.

    A normal write-context exit commits either the inserted `leased` task or a
    no-mutation duplicate/stale classification before this function returns.
    A database error, invalid setting, or durable inconsistency exits
    exceptionally and rolls back. The future AMQP layer must make no manual
    acknowledgement decision until it receives this normal return value.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    # Keep the transaction and its row locks limited to the first-claim SQL.
    # MinIO evidence, scratch I/O, CPU inference, AMQP acknowledgements, and
    # result writes belong to later isolated steps after this scope has ended.
    with database.write_cursor() as cursor:
        result = claim_adtof_task_for_delivery(
            cursor,
            message=message,
            lease_seconds=lease_seconds,
            uuid_factory=uuid_factory,
        )
    return result
