"""Atomically claim one due Basic Pitch retry and recover its strict request.

This is the transaction composition for the durable pre-model storage retry
path.  PostgreSQL first claims one due ``retry_scheduled`` task with a fresh
lease token, then the same transaction reads the matching immutable published
outbox event and reconstructs the normal strict request evidence.  Returning
only after the context exits normally guarantees that a later runtime never
starts MinIO/model work from a lease that was not committed with its evidence.

The module deliberately stops at that committed pair.  It does not poll or
publish RabbitMQ, call MinIO, invoke Basic Pitch, sleep, run a worker loop, or
create/update a Kubernetes resource.  A later runtime may pass the pair to the
existing pre-model execution path, whose guarded ``leased -> running``
transition remains the final permission to start the model.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from app.messaging.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.db.recovery_request import read_current_basic_pitch_recovery_request
from app.db.task_lease import (
    DEFAULT_BASIC_PITCH_LEASE_SECONDS,
    MAX_BASIC_PITCH_TASK_ATTEMPTS,
    BasicPitchTaskLease,
    DatabaseCursor,
    claim_next_due_basic_pitch_retry,
)


class BasicPitchDueRetryRecoveryProtocolError(RuntimeError):
    """A claimed retry and reconstructed request cannot form one safe pair."""


class BasicPitchDueRetryRecoveryInconsistency(RuntimeError):
    """A freshly claimed task had no matching current published outbox event.

    This is intentionally exceptional, rather than a normal empty recovery
    result.  The claim and read share one transaction, so returning normally
    would otherwise commit a new lease that has no strict request evidence for
    a later model run.  Raising makes the context roll back the claim.
    """


@dataclass(frozen=True)
class BasicPitchDueRetryRecovery:
    """One committed retry lease and its matching strict request evidence.

    Neither field is arbitrary caller data: the composition builds this only
    from the due-retry SQL claim and durable outbox reader.  Rechecking their
    shared identity prevents a future direct construction from pairing a lease
    for one stem with a request for another before the model path begins.
    """

    lease: BasicPitchTaskLease
    message: BasicPitchRequestedMessage

    def __post_init__(self) -> None:
        """Require the recovery-only attempt range and one immutable identity."""

        if (
            not isinstance(self.lease, BasicPitchTaskLease)
            or not isinstance(self.message, BasicPitchRequestedMessage)
            or type(self.lease.attempt_count) is not int
            or not 2 <= self.lease.attempt_count <= MAX_BASIC_PITCH_TASK_ATTEMPTS
            or self.lease.job_id != self.message.job_id
            or self.lease.stem_name != self.message.stem_name
            or self.lease.request_event_id != self.message.event_id
            or self.lease.input_bucket != self.message.stem_bucket
            or self.lease.input_object_key != self.message.stem_object_key
        ):
            raise BasicPitchDueRetryRecoveryProtocolError(
                "Basic Pitch due-retry recovery evidence is invalid."
            )


class BasicPitchDueRetryRecoveryDatabase(Protocol):
    """The one restricted write-transaction capability recovery needs."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a short cursor that commits normally and rolls back on errors."""


def recover_one_due_basic_pitch_retry(
    *,
    database: BasicPitchDueRetryRecoveryDatabase,
    lease_seconds: int = DEFAULT_BASIC_PITCH_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> BasicPitchDueRetryRecovery | None:
    """Commit one due retry lease/evidence pair, or report normal idle recovery.

    ``None`` means no due retry existed when PostgreSQL evaluated the indexed
    claim.  It commits no mutation and a caller may take its ordinary idle
    action.  A non-``None`` result becomes visible only after the one outer
    context commits both the fresh lease and its matching strict outbox read.

    A ``None`` evidence read after a successful claim is *not* ordinary idle:
    it indicates missing/inconsistent durable state.  This function raises to
    roll back the just-created lease rather than stranding it.  Database,
    protocol, and reader errors likewise escape so the context rolls back.

    The composition does not poll/acknowledge/publish RabbitMQ, access MinIO,
    run the CPU model, sleep, or create a Kubernetes resource.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    # The two pure adapters must share precisely this short transaction.  If
    # they were separate commits, a stale/changed event could leave a valid
    # recovery lease without the strict request needed to execute it safely.
    with database.write_cursor() as cursor:
        lease = claim_next_due_basic_pitch_retry(
            cursor,
            lease_seconds=lease_seconds,
            uuid_factory=uuid_factory,
        )
        if lease is None:
            return None
        message = read_current_basic_pitch_recovery_request(cursor, lease=lease)
        if message is None:
            raise BasicPitchDueRetryRecoveryInconsistency(
                "Basic Pitch due-retry recovery lost its matching durable request."
            )
        recovery = BasicPitchDueRetryRecovery(lease=lease, message=message)
    return recovery
