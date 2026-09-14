"""Start ADTOF work from one committed expired-lease recovery pair.

Normal ADTOF work reaches the post-claim success coordinator through an
``ACKNOWLEDGED_LEASE`` result, because it began with a RabbitMQ delivery. An
expired active lease is different: its original delivery was acknowledged
before the Pod failed, and its replacement is reconstructed solely from
PostgreSQL's immutable published outbox event. This gate accepts only that
committed recovery pair and deliberately carries no invented broker status.

Before it delegates, the gate repeats the direct-message and fresh recovery
lease invariants and verifies that event, Job, drums object, and stem identity
are exactly the same on both halves. It contains no storage/database/broker
operation itself. The existing coordinator still owns MinIO preflight, the
guarded ``leased`` -> ``running`` transition, CPU inference, upload, and
guarded completion.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.adtof_requested_message import ADTOF_STEM_NAME, LOCAL_UPLOADS_BUCKET, ADTOFRequestedMessage
from app.claimed_task_success import (
    ADTOFClaimedTaskSuccess,
    ADTOFClaimedTaskSuccessDatabase,
    ADTOFClaimedTaskSuccessStorageClient,
    execute_claimed_adtof_task_success_path,
)
from app.recovery import ADTOFRecoveredTask
from app.task_claim import (
    MAX_ADTOF_TASK_ATTEMPTS,
    ADTOFTaskClaimProtocolError,
    ADTOFTaskLease,
    validate_adtof_requested_message,
)


class ADTOFRecoveredTaskExecutionError(RuntimeError):
    """A purported recovery pair is not safe to pass to the success path.

    This fixed control-flow category intentionally reveals no task, object,
    event, token, credential, or database detail. It does not choose a retry
    or terminal transition; a future supervisor will make that decision after
    its reviewed failure classifier sees the boundary outcome.
    """


def _error() -> ADTOFRecoveredTaskExecutionError:
    """Return one redacted error for forged or mismatched recovery evidence."""

    return ADTOFRecoveredTaskExecutionError("ADTOF recovered task evidence is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical UUID text before comparing durable identity fields."""

    if not isinstance(value, str):
        raise _error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _error() from error
    if canonical != value:
        raise _error()
    return canonical


def _validated_recovery_pair(value: object) -> tuple[ADTOFTaskLease, ADTOFRequestedMessage]:
    """Accept only fresh recovery proof that still binds task and event together."""

    if not isinstance(value, ADTOFRecoveredTask):
        raise _error()
    lease = value.lease
    message = value.message
    if not isinstance(lease, ADTOFTaskLease) or not isinstance(message, ADTOFRequestedMessage):
        raise _error()

    # Reuse the normal-delivery validator so recovery cannot become a weaker
    # route into a private object. It returns canonical request UUID text.
    try:
        validated_message = validate_adtof_requested_message(message)
    except (ADTOFTaskClaimProtocolError, TypeError) as error:
        raise _error() from error

    job_id = _canonical_uuid(lease.job_id)
    request_event_id = _canonical_uuid(lease.request_event_id)
    _canonical_uuid(lease.task_id)
    _canonical_uuid(lease.lease_token)
    if (
        lease.stem_name != ADTOF_STEM_NAME
        or lease.stem_mode not in {"4-stems", "6-stems"}
        or lease.input_bucket != LOCAL_UPLOADS_BUCKET
        or lease.input_object_key != f"stems/{job_id}/{ADTOF_STEM_NAME}.wav"
        or type(lease.attempt_count) is not int
        or not 2 <= lease.attempt_count <= MAX_ADTOF_TASK_ATTEMPTS
        or not isinstance(lease.lease_expires_at, datetime)
        or lease.lease_expires_at.tzinfo is None
        or request_event_id != validated_message.event_id
        or job_id != validated_message.job_id
        or lease.stem_name != validated_message.stem_name
        or lease.input_bucket != validated_message.stem_bucket
        or lease.input_object_key != validated_message.stem_object_key
    ):
        raise _error()
    return lease, validated_message


def execute_recovered_adtof_task(
    recovered_task: ADTOFRecoveredTask,
    *,
    database: ADTOFClaimedTaskSuccessDatabase,
    storage_client: ADTOFClaimedTaskSuccessStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
) -> ADTOFClaimedTaskSuccess:
    """Run one committed recovery pair through the ordinary post-claim path.

    The function does not scan for leases, retain a database cursor, or make a
    RabbitMQ call. Its only role is to ensure a recovery pair cannot be forged
    or cross-wired before it reaches the same storage/CPU/finalization path as
    an acknowledged normal delivery. Operational errors propagate unchanged to
    the later lifecycle supervisor.
    """

    lease, message = _validated_recovery_pair(recovered_task)
    return execute_claimed_adtof_task_success_path(
        database=database,
        storage_client=storage_client,
        message=message,
        lease=lease,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )
