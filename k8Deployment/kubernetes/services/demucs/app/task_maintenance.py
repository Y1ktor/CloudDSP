"""One-at-a-time durable recovery and renewal compositions for Demucs tasks.

``task_lease.py`` holds the pure PostgreSQL SQL decisions.  This module gives
the two maintenance decisions exactly one short commit-or-rollback transaction
each, just as ``first_claim.py`` does for a new RabbitMQ delivery.  A later
supervisor may call these functions, but it must not wrap them in an AMQP
acknowledgement, MinIO request, FFprobe/Demucs run, sleep loop, result update,
or Kubernetes API action.

Recovery and renewal are separate calls because they protect different cases:
recovery gives one due retry or expired task a new lease, while renewal extends
only a lease token the current worker still owns.  Both return only after their
database context has finished, so PostgreSQL's durable answer is known before a
future caller starts external work.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.task_lease import (
    DEFAULT_DEMUCS_LEASE_SECONDS,
    DatabaseCursor,
    DemucsTaskLease,
    claim_next_recoverable_demucs_task,
    renew_demucs_task_lease,
)


class DemucsTaskMaintenanceDatabase(Protocol):
    """The one transaction capability required for task recovery or renewal.

    The concrete ``PsycopgDemucsDatabase`` provides this interface
    structurally.  Keeping only the tiny cursor-context contract here prevents
    maintenance logic from learning about a connection pool, RabbitMQ, or a
    Kubernetes Pod, and lets the safety ordering be unit-tested without a
    running database.
    """

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield one dictionary-row cursor in a commit-or-rollback scope."""


def recover_one_demucs_task(
    *,
    database: DemucsTaskMaintenanceDatabase,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> DemucsTaskLease | None:
    """Commit one due-task recovery lease, or commit an idle no-work result.

    ``None`` is a normal, committed idle scan: it means no retry-scheduled or
    expired task was eligible when PostgreSQL evaluated the indexed claim.  A
    returned lease belongs to this worker only while its token remains current;
    a later worker must renew it before it expires.  Any invalid row or database
    failure leaves this scope through an exception, so the transaction rolls
    back and a future supervisor can apply its bounded retry policy.
    """

    # The `with` scope ends before a future supervisor sleeps, starts external
    # audio work, or acknowledges any broker delivery.  Therefore the durable
    # lease and PostgreSQL row locks cannot outlive this tiny decision.
    with database.write_cursor() as cursor:
        return claim_next_recoverable_demucs_task(
            cursor,
            lease_seconds=lease_seconds,
            uuid_factory=uuid_factory,
        )


def renew_one_demucs_task_lease(
    *,
    database: DemucsTaskMaintenanceDatabase,
    task_id: str,
    lease_token: str,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
) -> datetime | None:
    """Commit one guarded lease extension or return a committed ownership loss.

    ``None`` is a safe, durable signal that the task is inactive, expired, or
    now owned by a recovery worker with a different token.  The future Demucs
    runtime must stop processing immediately in that case; it must never write
    artifacts or a result after PostgreSQL has withdrawn its ownership.
    """

    # Renewal changes only one guarded timestamp.  Do not hold this transaction
    # while a model runs: a later runtime invokes this small operation between
    # bounded processing intervals and reacts to its committed return value.
    with database.write_cursor() as cursor:
        return renew_demucs_task_lease(
            cursor,
            task_id=task_id,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        )
