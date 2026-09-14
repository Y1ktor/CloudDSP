"""Start ADTOF work only after RabbitMQ accepted a durable claimed delivery.

``amqp_manual_ack.py`` owns exactly one broker interaction.  It parses an
``adtof.requested`` delivery, commits PostgreSQL's first-claim result, and
acknowledges RabbitMQ.  Only its ``ACKNOWLEDGED_LEASE`` result proves both
facts needed to begin the long-running success path:

* PostgreSQL committed the canonical ``(job_id, 'adtof', 'drums')`` lease; and
* RabbitMQ accepted acknowledgement for the delivery that created that lease.

This source-only handoff validates that evidence before delegating to the
post-claim success coordinator.  It deliberately receives no AMQP channel,
delivery tag, raw body, database cursor, retry setting, or Kubernetes client.
Consequently it cannot acknowledge/reject/requeue a message, open its own
transaction, select failure policy, start a consumer loop, or change cluster
state.  A later supervisor owns those separate responsibilities.
"""

from __future__ import annotations

from pathlib import Path

from app.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.adtof_requested_message import ADTOFRequestedMessage
from app.amqp_manual_ack import ADTOFConsumeOneOutcome, ADTOFConsumeOneResult
from app.claimed_task_success import (
    ADTOFClaimedTaskSuccess,
    ADTOFClaimedTaskSuccessDatabase,
    ADTOFClaimedTaskSuccessStorageClient,
    execute_claimed_adtof_task_success_path,
)
from app.task_claim import ADTOFTaskLease


class ADTOFAcknowledgedLeaseExecutionError(RuntimeError):
    """A receive result does not prove permission to start ADTOF CPU work.

    This fixed control-flow error does not decide a retry or DLQ outcome.  Idle,
    duplicate, stale, and malformed results have no current execution lease,
    so a future long-running supervisor must handle them without treating them
    as an ADTOF model request.
    """


def execute_acknowledged_adtof_lease(
    receive_result: ADTOFConsumeOneResult,
    *,
    database: ADTOFClaimedTaskSuccessDatabase,
    storage_client: ADTOFClaimedTaskSuccessStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
) -> ADTOFClaimedTaskSuccess:
    """Delegate exactly one acknowledged lease to the post-claim coordinator.

    Rechecking the outcome and both evidence types protects this boundary from
    unsafe test doubles or a future transport regression.  The coordinator
    itself keeps MinIO proof, short task-state transactions, CPU execution,
    and finalization in their reviewed order.  This function performs no
    storage/database/broker operation of its own.
    """

    if not isinstance(receive_result, ADTOFConsumeOneResult):
        raise TypeError("receive_result must be ADTOFConsumeOneResult.")
    if receive_result.outcome is not ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE:
        raise ADTOFAcknowledgedLeaseExecutionError(
            "ADTOF execution requires an acknowledged task lease."
        )

    # The manual-ack result's constructor requires evidence for this outcome,
    # but retain these runtime checks so direct construction or a future change
    # cannot send `None` or foreign task provenance into object/model work.
    lease = receive_result.lease
    message = receive_result.message
    if not isinstance(lease, ADTOFTaskLease) or not isinstance(message, ADTOFRequestedMessage):
        raise ADTOFAcknowledgedLeaseExecutionError(
            "ADTOF execution requires an acknowledged task lease."
        )

    return execute_claimed_adtof_task_success_path(
        database=database,
        storage_client=storage_client,
        message=message,
        lease=lease,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )
