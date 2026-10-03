"""Reconstruct strict Demucs input evidence for one recovered task lease.

RabbitMQ was already acknowledged when the original ``demucs.requested``
delivery first acquired a durable task.  A due retry or expired lease therefore
has no new AMQP frame to parse.  This read-only PostgreSQL boundary rebuilds a
normal :class:`DemucsRequestedMessage` from the exact immutable, *published*
outbox event that originally authorized the task.

The restricted Demucs role intentionally cannot read arbitrary
``outbox_events.payload`` values.  The function below therefore calls one
reviewed PostgreSQL ``SECURITY DEFINER`` verifier.  It checks the immutable
payload *inside PostgreSQL* and returns only a boolean.  This lets recovery
prove the original request still matches without widening the worker into a
general event-payload reader.

The caller must run this query immediately after the due/expired claim in the
same short PostgreSQL transaction.  This module does not claim or start a
task, open/commit a transaction, acknowledge/publish RabbitMQ, read MinIO, run
Demucs, sleep, or make a Kubernetes action.  A later composition will commit
only a matched lease/evidence pair, then send that pair through the ordinary
pre-model execution path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from uuid import UUID

from app.messaging.demucs_requested_message import (
    DemucsRequestContractError,
    DemucsRequestedMessage,
    validate_demucs_requested_message,
)
from app.db.task_lease import MAX_DEMUCS_TASK_ATTEMPTS, DatabaseCursor, DemucsTaskLease


class DemucsRecoveryRequestProtocolError(RuntimeError):
    """The recovered lease or its durable authorization evidence is unsafe.

    This category deliberately omits private source keys, raw JSONB, database
    diagnostics, credentials, and tenant identity.  Its later transaction
    composition must roll back rather than commit a lease it cannot execute
    from proven inputs.
    """


# The recovery claim already owns the candidate row with `FOR UPDATE SKIP
# LOCKED`.  The database function receives the complete lease coordinate and
# checks the matching immutable published event and its exact v1 JSON payload
# under its owner, then returns one boolean to the restricted worker.  It is a
# SELECT—not another lock or lifecycle mutation.  PostgreSQL's clock, not the
# Pod clock, decides whether the freshly issued lease remains current.  The
# later `leased -> running` transition is still the final guard before source
# storage or a model process can be used.
READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL: Final[str] = """
    SELECT
      public.clouddsp_demucs_recovery_event_matches(
        %s::uuid,
        %s::uuid,
        %s::uuid,
        %s,
        %s,
        %s,
        %s::integer,
        %s::uuid
      ) AS recovery_event_matches
"""


def _error() -> DemucsRecoveryRequestProtocolError:
    """Return one redacted category for all unusable recovery evidence."""

    return DemucsRecoveryRequestProtocolError("Demucs recovery evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text before using it in a query."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _validated_recovery_lease(value: object) -> DemucsTaskLease:
    """Accept only a fresh recovery lease, never a first AMQP attempt.

    Attempt one belongs to the delivery parser/first-claim path.  A recovery
    lease starts at attempt two; enforcing that boundary prevents a caller from
    bypassing the AMQP contract by handing this reader a first-attempt lease.
    SQL checks current time, so this function intentionally does not create a
    competing local-clock definition of lease expiry.
    """

    if not isinstance(value, DemucsTaskLease):
        raise _error()
    job_id = _canonical_uuid(value.job_id)
    _canonical_uuid(value.task_id)
    _canonical_uuid(value.request_event_id)
    _canonical_uuid(value.lease_token)
    if (
        type(value.attempt_count) is not int
        or not 2 <= value.attempt_count <= MAX_DEMUCS_TASK_ATTEMPTS
    ):
        raise _error()
    try:
        # Validate all source fields before this function binds them as SQL
        # parameters.  A recovery lease is database evidence, but its values
        # still must obey the exact same local object-coordinate contract as a
        # first AMQP request; annotations alone provide no runtime type guard.
        validate_demucs_requested_message(
            DemucsRequestedMessage(
                event_id=value.request_event_id,
                job_id=job_id,
                source_bucket=value.input_bucket,
                source_object_key=value.input_object_key,
                stem_mode=value.stem_mode,
            )
        )
    except DemucsRequestContractError as error:
        raise _error() from error
    return value


def _message_from_verified_recovery_event(
    row: Mapping[str, object], *, lease: DemucsTaskLease
) -> DemucsRequestedMessage:
    """Rebuild strict input only after the database verifier returned ``true``.

    The verifier, not this restricted role, read the private JSONB payload and
    proved it contains precisely this lease's v1 Demucs request.  The worker
    can now safely rebuild the same typed message from immutable task columns
    that it is already allowed to read.  A false, null, or malformed result is
    unsafe recovery evidence and must cause the enclosing fresh-lease
    transaction to roll back.
    """

    if row.get("recovery_event_matches") is not True:
        raise _error()
    try:
        # This uses the same UUID, bucket/key, and stem-mode contract as a new
        # AMQP delivery.  PostgreSQL has already proven the corresponding
        # private event payload; the shared validator still checks that no
        # malformed task coordinate reaches MinIO or a model process.
        return validate_demucs_requested_message(
            DemucsRequestedMessage(
                event_id=lease.request_event_id,
                job_id=lease.job_id,
                source_bucket=lease.input_bucket,
                source_object_key=lease.input_object_key,
                stem_mode=lease.stem_mode,
            )
        )
    except DemucsRequestContractError as error:
        raise _error() from error


def read_current_demucs_recovery_request(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
) -> DemucsRequestedMessage | None:
    """Return strict request evidence for one current fresh recovery lease.

    ``None`` is normal ownership loss: PostgreSQL found no matching unexpired
    `leased` task/event pair for this exact token.  The caller must stop without
    contacting MinIO or starting Demucs.  A malformed lease or row raises so
    its surrounding short recovery transaction rolls back, leaving no new
    lease stranded without recoverable execution evidence.
    """

    if not callable(getattr(cursor, "execute", None)) or not callable(getattr(cursor, "fetchone", None)):
        raise TypeError("cursor must provide execute and fetchone.")
    validated_lease = _validated_recovery_lease(lease)
    task_id = _canonical_uuid(validated_lease.task_id)
    job_id = _canonical_uuid(validated_lease.job_id)
    request_event_id = _canonical_uuid(validated_lease.request_event_id)
    lease_token = _canonical_uuid(validated_lease.lease_token)
    cursor.execute(
        READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL,
        (
            task_id,
            job_id,
            request_event_id,
            validated_lease.input_bucket,
            validated_lease.input_object_key,
            validated_lease.stem_mode,
            validated_lease.attempt_count,
            lease_token,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise _error()
    return _message_from_verified_recovery_event(row, lease=validated_lease)
