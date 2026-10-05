"""Commit one Basic Pitch first-claim decision before any broker acknowledgement.

``task_lease.py`` deliberately contains only the pure, parameterized SQL
decision for a parser-validated ``basic-pitch.requested`` message.  This small
composition provides the missing short PostgreSQL write scope: it creates the
canonical per-stem lease or classifies a duplicate/stale delivery, then lets
the transaction commit before returning the result to a future AMQP layer.

It does not import Pika, receive a RabbitMQ frame, acknowledge/reject/retry a
delivery, access MinIO, invoke Basic Pitch, update a result, sleep, or call a
Kubernetes API.  The future AMQP consumer must make its acknowledgement
decision only after this function returns normally.  That ordering makes a
crash after commit and before acknowledgement a harmless duplicate delivery,
while a database error or durable inconsistency remains unacknowledged for its
separate recovery/failure policy.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID, uuid4

from app.messaging.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.db.task_lease import (
    DEFAULT_BASIC_PITCH_LEASE_SECONDS,
    DatabaseCursor,
    BasicPitchTaskClaimResult,
    claim_basic_pitch_task_for_delivery,
)


class BasicPitchTaskClaimDatabase(Protocol):
    """The sole database capability required by one first-claim attempt.

    ``PsycopgBasicPitchDatabase`` satisfies this protocol with its restricted
    credential and short commit-or-rollback transaction. Keeping this module
    structural avoids importing the concrete Psycopg wrapper and lets tests
    prove transaction ordering with an in-memory context manager.
    """

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield one dictionary-row cursor that commits on normal exit."""


def claim_first_basic_pitch_task(
    *,
    database: BasicPitchTaskClaimDatabase,
    message: BasicPitchRequestedMessage,
    lease_seconds: int = DEFAULT_BASIC_PITCH_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> BasicPitchTaskClaimResult:
    """Commit one durable claim/duplicate/stale result for an accepted request.

    The pure adapter checks the request, lease duration, current task, locked
    Job, published outbox evidence, and idempotency state. A normal context
    exit commits either the inserted ``leased`` task or its no-mutation
    duplicate/stale classification before this result becomes visible. An
    inconsistency, malformed row, or database error leaves the write context
    exceptionally and rolls back; the future AMQP consumer must not treat that
    delivery as acknowledgement-safe.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    # Keep lock time bounded to only the first-claim SQL. MinIO validation,
    # temporary-file work, CPU inference, broker acknowledgement, and task
    # completion occur in distinct phases and must never share this scope.
    with database.write_cursor() as cursor:
        result = claim_basic_pitch_task_for_delivery(
            cursor,
            message=message,
            lease_seconds=lease_seconds,
            uuid_factory=uuid_factory,
        )
    return result
