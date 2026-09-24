"""One-at-a-time durable recovery and renewal compositions for Demucs tasks.

``task_lease.py`` holds the pure PostgreSQL SQL decisions.  This module gives
the two maintenance decisions exactly one short commit-or-rollback transaction
each, just as ``first_claim.py`` does for a new RabbitMQ delivery.  A later
supervisor may call these functions, but it must not wrap them in an AMQP
acknowledgement, MinIO request, FFprobe/Demucs run, sleep loop, result update,
or Kubernetes API action.

Terminalization, recovery, and renewal are separate calls because they protect
different cases: terminalization records an overdue run or expired third attempt, recovery
commits a matched lease/request pair for a reclaimable task, and renewal extends
only a lease token the current worker still owns. All return only after their
database context has finished, so PostgreSQL's durable answer is known before a
future caller starts external work.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.demucs_requested_message import (
    DemucsRequestContractError,
    DemucsRequestedMessage,
    validate_demucs_requested_message,
)
from app.recovery_request import read_current_demucs_recovery_request
from app.task_lease import (
    DEFAULT_DEMUCS_LEASE_SECONDS,
    DatabaseCursor,
    DemucsExpiredLeaseTerminalization,
    DemucsTaskLease,
    MAX_DEMUCS_TASK_ATTEMPTS,
    claim_next_recoverable_demucs_task,
    finalize_next_expired_exhausted_demucs_task,
    finalize_next_overdue_demucs_task,
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


class DemucsRecoveredTaskProtocolError(RuntimeError):
    """A lease and reconstructed request do not form safe recovery evidence."""


@dataclass(frozen=True)
class DemucsRecoveredTask:
    """One committed recovery lease paired with its strict source request.

    Neither field is arbitrary input from a future worker loop. The recovery
    composition creates this value only after the durable claim and immutable
    outbox read agree in one transaction. The normal source-preflight and
    `leased -> running` transition still run later; this pair merely supplies
    the evidence that replaces the acknowledged RabbitMQ delivery.
    """

    lease: DemucsTaskLease
    message: DemucsRequestedMessage

    def __post_init__(self) -> None:
        """Reject mixed, first-attempt, or unsafe direct-construction pairs."""

        if (
            not isinstance(self.lease, DemucsTaskLease)
            or not isinstance(self.message, DemucsRequestedMessage)
            or type(self.lease.attempt_count) is not int
            or not 2 <= self.lease.attempt_count <= MAX_DEMUCS_TASK_ATTEMPTS
            or self.lease.job_id != self.message.job_id
            or self.lease.request_event_id != self.message.event_id
            or self.lease.input_bucket != self.message.source_bucket
            or self.lease.input_object_key != self.message.source_object_key
            or self.lease.stem_mode != self.message.stem_mode
        ):
            raise DemucsRecoveredTaskProtocolError("Demucs recovery pair is invalid.")
        try:
            # A frozen dataclass can still be built directly by later code.
            # Reusing the strict source contract keeps this pair from becoming
            # an alternate path to an unsafe private object coordinate.
            validate_demucs_requested_message(self.message)
        except DemucsRequestContractError as error:
            raise DemucsRecoveredTaskProtocolError("Demucs recovery pair is invalid.") from error


class _RecoveryEvidenceUnavailable(Exception):
    """Force rollback when a fresh lease lacks current strict event evidence.

    This private sentinel never escapes the composition. Its sole purpose is
    to leave the concrete transaction context exceptionally, so it rolls back
    the fresh claim before the caller receives the normal no-safe-work result.
    """


def terminalize_one_expired_exhausted_demucs_task(
    *,
    database: DemucsTaskMaintenanceDatabase,
) -> DemucsExpiredLeaseTerminalization | None:
    """Commit one overdue or expired-final task/Job failure before recovery.

    An overdue run cannot receive another CPU attempt, and an expired final
    attempt cannot receive a fourth token. This short
    transaction therefore runs separately from recovery: a committed terminal
    result is durable progress, while ``None`` is an idle/no-row condition that
    may include a concurrent winner, a deleted/expired Job, or no candidate.
    The caller must never translate either outcome into MinIO/model work.

    The context ends before another recovery scan, RabbitMQ action, storage
    operation, CPU process, or sleep. An adapter/database failure escapes and
    rolls back. This function does not classify a runtime exception or create a
    retry schedule; it records only durable deadline or lease evidence.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    with database.write_cursor() as cursor:
        # First stop any task whose durable model start crossed the fixed
        # 12-minute budget, even if a vanished Pod's 15-minute lease has not
        # expired. Only then consider the older third-attempt lease policy.
        terminalization = finalize_next_overdue_demucs_task(cursor)
        if terminalization is None:
            terminalization = finalize_next_expired_exhausted_demucs_task(cursor)
        # Keep this check inside the transaction. A future substituted adapter
        # must not commit a different object and have it reported as an atomic
        # Job/task terminal state merely because it was non-None.
        if terminalization is not None and not isinstance(
            terminalization,
            DemucsExpiredLeaseTerminalization,
        ):
            raise TypeError("Demucs expired-lease terminalization is invalid.")
    return terminalization


def recover_one_demucs_task(
    *,
    database: DemucsTaskMaintenanceDatabase,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> DemucsRecoveredTask | None:
    """Commit one due-task lease/evidence pair, or report no safe work.

    ``None`` is a normal, committed idle scan: it means no retry-scheduled or
    expired task was eligible when PostgreSQL evaluated the indexed claim. It
    also follows a rollback when a just-claimed lease no longer has a matching
    current published event: returning a bare lease would otherwise strand an
    unusable task. A returned pair belongs to this worker only while its token
    remains current; a later runtime must still complete the guarded pre-model
    start and renew before it expires. Other invalid row/evidence or database
    failures escape, so the transaction rolls back for a future bounded retry.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    try:
        # The claim's `FOR UPDATE SKIP LOCKED` row lock remains held through
        # the strict outbox read. No broker, storage, model, or sleep action
        # may enter this scope, so database locks and a fresh lease cannot be
        # committed before all execution evidence is present.
        with database.write_cursor() as cursor:
            lease = claim_next_recoverable_demucs_task(
                cursor,
                lease_seconds=lease_seconds,
                uuid_factory=uuid_factory,
            )
            if lease is None:
                return None
            message = read_current_demucs_recovery_request(cursor, lease=lease)
            if message is None:
                raise _RecoveryEvidenceUnavailable()
            recovered = DemucsRecoveredTask(lease=lease, message=message)
    except _RecoveryEvidenceUnavailable:
        # The context above has already rolled back the newly issued token.
        # Treat its lost evidence as no safe work, never as permission to run
        # from a stale caller-provided message or to contact private storage.
        return None
    return recovered


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
