"""Commit one complete ADTOF expired-lease recovery handoff.

An ADTOF worker can lose its Pod after the original RabbitMQ delivery has been
acknowledged. ``task_claim.py`` can then grant one fresh PostgreSQL lease, but
that lease is unsafe to commit by itself: no raw broker frame remains from
which to rebuild the exact drums request. This composition keeps the expired
lease claim and the published-outbox reader in one short database transaction.
It returns a pair only after that context commits.

No expired candidate is ordinary idle. If the claim succeeds but the reader
finds that the new lease is no longer current, this module deliberately raises
an internal sentinel while still inside the context manager. The concrete
database adapter therefore rolls back the new lease, and the sentinel becomes
the caller-visible ``None`` only after rollback. This prevents a later scan
from finding a committed recovery lease with no strict execution evidence.

The composition does not receive, acknowledge, reject, or publish RabbitMQ;
it does not contact MinIO, run ADTOF, sleep, start a loop, build an image, or
use the Kubernetes API. A later shutdown-aware supervisor can pass a committed
pair to the ordinary post-claim execution coordinator.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from app.adtof_requested_message import ADTOFRequestedMessage
from app.recovery_request import read_current_adtof_recovery_request
from app.task_claim import (
    DEFAULT_ADTOF_LEASE_SECONDS,
    ADTOFTaskLease,
    DatabaseCursor,
    claim_next_expired_adtof_task,
)


class ADTOFRecoveryDatabase(Protocol):
    """The one transactional capability needed to recover one ADTOF task.

    The application owns neither a connection nor commit/rollback calls. The
    concrete PostgreSQL adapter must commit on a normal context exit and roll
    back on an exception, which is what makes a missing recovery request undo
    the otherwise-successful lease claim below.
    """

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield one short dictionary-row write transaction."""


@dataclass(frozen=True)
class ADTOFRecoveredTask:
    """One committed recovery lease paired with its strict request evidence.

    ``lease`` is the fresh PostgreSQL ownership token and ``message`` is the
    immutable event evidence reconstructed from the same transaction. Neither
    field is a RabbitMQ delivery, acknowledgement command, storage client, or
    permission to skip the normal MinIO preflight and guarded task start.
    """

    lease: ADTOFTaskLease
    message: ADTOFRequestedMessage


class _RecoveryOwnershipLost(Exception):
    """Force rollback for a claimed lease whose evidence cannot be re-read.

    This exception never crosses the public function boundary. It exists only
    so a normal ownership-loss result still leaves the transaction context via
    its exceptional rollback path instead of committing a lease without a
    message that the worker may safely execute.
    """


def recover_one_expired_adtof_task(
    *,
    database: ADTOFRecoveryDatabase,
    lease_seconds: int = DEFAULT_ADTOF_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> ADTOFRecoveredTask | None:
    """Return one committed recovery pair, or ``None`` when no safe work exists.

    The claim and strict outbox read intentionally share *one* cursor. An idle
    scan commits normally because it made no mutation. A fresh claim paired
    with a current published event also commits normally. If evidence disappears
    after the claim, the private sentinel causes rollback and this function
    returns ``None`` only after that rollback completes. Database/protocol
    errors propagate and likewise make the context roll back.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    try:
        # The claim's row lock survives through the reader query. Keep this
        # scope limited to PostgreSQL decisions: MinIO/CPU work must happen
        # after a complete pair has committed and the lock is released.
        with database.write_cursor() as cursor:
            lease = claim_next_expired_adtof_task(
                cursor,
                lease_seconds=lease_seconds,
                uuid_factory=uuid_factory,
            )
            if lease is None:
                return None

            message = read_current_adtof_recovery_request(cursor, lease=lease)
            if message is None:
                raise _RecoveryOwnershipLost()
            recovered = ADTOFRecoveredTask(lease=lease, message=message)
    except _RecoveryOwnershipLost:
        # The context has already rolled back the fresh lease. Treat this like
        # a concurrent owner/terminal transition rather than an application
        # failure; a future supervisor may continue its bounded idle loop.
        return None
    return recovered
