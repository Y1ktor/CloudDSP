"""Run Basic Pitch after any reviewed current durable lease decision.

``amqp_manual_ack.py`` completes the single-delivery broker decision first.
Only its ``ACKNOWLEDGED_LEASE`` outcome proves the two prerequisites required
for the existing execution coordinator:

* PostgreSQL committed the canonical `(job_id, basic-pitch, stem_name)` lease;
  and
* RabbitMQ accepted acknowledgement of the delivery that produced that lease.

The acknowledged-delivery gate below proves those particular prerequisites,
then invokes the shared post-lease execution policy. The same policy is also
safe for a separately committed PostgreSQL retry-recovery lease/request pair:
it has no broker operation and applies the same durable terminal/retry rules.
Neither entry point receives AMQP frames, opens/closes a RabbitMQ connection,
acknowledges/rejects/retries a delivery, opens a transaction itself, schedules
a loop, or changes Kubernetes state. The existing coordinator retains
ownership of MinIO/model/short-transaction ordering.
"""

from __future__ import annotations

from pathlib import Path

from app.messaging.amqp_manual_ack import BasicPitchConsumeOneOutcome, BasicPitchConsumeOneResult
from app.processing.basic_pitch_process import BasicPitchProcessRunner, DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS
from app.runtime.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecutionOutcome,
    BasicPitchClaimedTaskExecution,
    BasicPitchTaskExecutionDatabase,
    BasicPitchTaskExecutionStorageClient,
    execute_claimed_basic_pitch_task,
)
from app.messaging.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.runtime.stem_failure_classification import classify_basic_pitch_pre_model_terminal_failure
from app.runtime.stem_retry_handling import (
    BasicPitchPreModelRetryHandlingDisposition,
    handle_basic_pitch_pre_model_storage_retry,
)
from app.db.stem_task_terminal_failure_commit import commit_terminal_basic_pitch_stem_failure
from app.db.task_lease import BasicPitchTaskLease


class BasicPitchAcknowledgedLeaseExecutionError(RuntimeError):
    """A normal receive result is not eligible to start worker-side execution.

    This fixed control-flow category is not a retry policy. Idle, duplicate,
    stale, and malformed outcomes intentionally have no current ownership, so
    a future supervisor must handle those paths without treating this error as
    media work or making another broker action.
    """


def execute_current_basic_pitch_lease(
    *,
    database: BasicPitchTaskExecutionDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    message: BasicPitchRequestedMessage,
    lease: BasicPitchTaskLease,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
) -> BasicPitchClaimedTaskExecution:
    """Execute one current lease with the shared pre-model failure policy.

    The caller must have already established a reviewed current lease and its
    matching strict request—either the acknowledged normal-delivery gate or
    the committed due-retry recovery pair. This helper has no knowledge of
    which route supplied them, so it cannot make a RabbitMQ decision. Its
    defensive type checks ensure test doubles or future direct calls cannot
    begin MinIO/model work without both pieces of evidence.

    A known permanent stem metadata/download-consistency error is the one
    exception: the already-acknowledged lease is changed to a durable terminal
    task result in a short PostgreSQL transaction and returned normally. A
    reviewed temporary MinIO failure before model start is instead durably
    scheduled on attempts one/two or terminally exhausted on attempt three.
    Every other storage/database/process exception propagates unchanged. The
    future long-running supervisor, not this gate, will decide recovery
    re-delivery and readiness/health behavior for those unclassified failures.
    """

    if not isinstance(lease, BasicPitchTaskLease) or not isinstance(message, BasicPitchRequestedMessage):
        raise TypeError("message and lease must be current Basic Pitch task evidence.")

    # The coordinator itself starts with HeadObject, manages temporary scratch
    # lifetime, runs the model, verifies/uploads MIDI, and opens its own short
    # start/completion transactions. No RabbitMQ action can occur here because
    # neither a channel nor delivery tag is accepted.
    try:
        return execute_claimed_basic_pitch_task(
            database=database,
            storage_client=storage_client,
            message=message,
            lease=lease,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
    except Exception as execution_error:
        # The permanent classifier has priority: a known immutable input
        # mismatch must never be reclassified as a temporary storage outage.
        failure_code = classify_basic_pitch_pre_model_terminal_failure(execution_error)
        if failure_code is not None:
            terminal_failure = commit_terminal_basic_pitch_stem_failure(
                database=database,
                lease=lease,
                failure_code=failure_code,
            )
            if terminal_failure is None:
                # A recovery/expiry race may have removed this worker's right
                # to write its result. Do not replace a newer durable decision.
                return BasicPitchClaimedTaskExecution(
                    outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
                )
            return BasicPitchClaimedTaskExecution(
                outcome=BasicPitchClaimedTaskExecutionOutcome.TERMINAL_FAILURE,
                terminal_failure=terminal_failure,
            )

        # The retry handoff recognizes only the two reviewed pre-model MinIO
        # wrappers. It commits either a later retry time or final exhaustion;
        # all other errors produce ``UNCLASSIFIED`` without SQL and re-raise
        # below so their own policy remains visible to the supervisor.
        retry_handling = handle_basic_pitch_pre_model_storage_retry(
            execution_error,
            database=database,
            lease=lease,
        )
        if retry_handling.disposition is BasicPitchPreModelRetryHandlingDisposition.UNCLASSIFIED:
            raise
        if retry_handling.disposition is BasicPitchPreModelRetryHandlingDisposition.RETRY_SCHEDULED:
            return BasicPitchClaimedTaskExecution(
                outcome=BasicPitchClaimedTaskExecutionOutcome.RETRY_SCHEDULED,
                retry_schedule=retry_handling.retry_schedule,
            )
        if retry_handling.disposition is BasicPitchPreModelRetryHandlingDisposition.RETRY_EXHAUSTED:
            return BasicPitchClaimedTaskExecution(
                outcome=BasicPitchClaimedTaskExecutionOutcome.RETRY_EXHAUSTED,
                retry_exhaustion=retry_handling.retry_exhaustion,
            )
        if retry_handling.disposition is BasicPitchPreModelRetryHandlingDisposition.NO_DURABLE_RESULT:
            return BasicPitchClaimedTaskExecution(
                outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
            )
        raise RuntimeError("Basic Pitch pre-model retry handling result is invalid.")


def execute_acknowledged_basic_pitch_lease(
    receive_result: BasicPitchConsumeOneResult,
    *,
    database: BasicPitchTaskExecutionDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
) -> BasicPitchClaimedTaskExecution:
    """Run the shared policy only for one broker-acknowledged current lease.

    The result dataclass and the defensive checks protect this handoff from
    unsafe object construction: idle, duplicate, stale, and malformed broker
    outcomes cannot reach MinIO/model work. Once this gate proves that the
    durable first claim committed *and* RabbitMQ accepted acknowledgement, it
    delegates to the transport-independent post-lease policy above.
    """

    if not isinstance(receive_result, BasicPitchConsumeOneResult):
        raise TypeError("receive_result must be BasicPitchConsumeOneResult.")
    if receive_result.outcome is not BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE:
        raise BasicPitchAcknowledgedLeaseExecutionError(
            "Basic Pitch execution requires an acknowledged task lease."
        )
    lease = receive_result.lease
    message = receive_result.message
    if not isinstance(lease, BasicPitchTaskLease) or not isinstance(message, BasicPitchRequestedMessage):
        raise BasicPitchAcknowledgedLeaseExecutionError(
            "Basic Pitch execution requires an acknowledged task lease."
        )
    return execute_current_basic_pitch_lease(
        database=database,
        storage_client=storage_client,
        message=message,
        lease=lease,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )
