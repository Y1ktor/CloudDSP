"""One short durable first-claim composition for an already parsed Demucs request.

The pure SQL decision in :mod:`app.task_lease` deliberately knows nothing
about a database connection or transaction.  This narrow composition supplies
exactly that missing boundary: it opens one short PostgreSQL write scope,
creates or classifies the canonical task lease, and lets the scope commit or
roll back before it returns to its future AMQP caller.

It intentionally does **not** consume a RabbitMQ message, acknowledge or
reject a delivery, read MinIO, run FFprobe/Demucs, update a result, sleep, or
create a Kubernetes resource.  In particular, the later AMQP layer must make
its acknowledgement decision only after this function has returned: Python
exits the ``with`` block (therefore commits on normal exit) before exposing the
result to that caller.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID, uuid4

from app.demucs_requested_message import DemucsRequestedMessage
from app.task_lease import (
    DEFAULT_DEMUCS_LEASE_SECONDS,
    DatabaseCursor,
    DemucsTaskClaimResult,
    claim_demucs_task_for_delivery,
)


class DemucsTaskClaimDatabase(Protocol):
    """The one transaction capability required by a first-claim attempt.

    ``PsycopgDemucsDatabase`` satisfies this structural interface without this
    transport-independent module importing the concrete driver wrapper.  That
    keeps the composition straightforward to test with an in-memory context
    manager and prevents a future broker loop from owning a long-lived database
    connection.
    """

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield one dictionary-row cursor in a commit-or-rollback scope."""


def claim_first_demucs_task(
    *,
    database: DemucsTaskClaimDatabase,
    message: DemucsRequestedMessage,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> DemucsTaskClaimResult:
    """Durably create one task lease or return a safe duplicate/stale result.

    The pure adapter validates the message, lease duration, database rows, and
    idempotency decision.  A normal context exit commits either the new
    ``CLAIMED`` lease or the no-mutation duplicate/stale classification.  An
    inconsistency or database error escapes the context so it rolls back; the
    future AMQP consumer must then leave that delivery unacknowledged rather
    than treating it as safely processed.
    """

    # Keep this transaction intentionally tiny.  Its context finishes before
    # any later broker acknowledgement, storage request, CPU work, or model
    # invocation can begin, so a slow external operation never holds Job/task
    # row locks or turns a database lease into an unbounded resource reservation.
    with database.write_cursor() as cursor:
        return claim_demucs_task_for_delivery(
            cursor,
            message=message,
            lease_seconds=lease_seconds,
            uuid_factory=uuid_factory,
        )
