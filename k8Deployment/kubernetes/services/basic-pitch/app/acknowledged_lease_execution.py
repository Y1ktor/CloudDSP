"""Gate Basic Pitch execution behind an acknowledged durable lease result.

``amqp_manual_ack.py`` completes the single-delivery broker decision first.
Only its ``ACKNOWLEDGED_LEASE`` outcome proves the two prerequisites required
for the existing execution coordinator:

* PostgreSQL committed the canonical `(job_id, basic-pitch, stem_name)` lease;
  and
* RabbitMQ accepted acknowledgement of the delivery that produced that lease.

This small handoff then passes the same parser-validated request message and
lease to ``basic_pitch_task_execution.py``. It does not receive AMQP frames,
open/close a RabbitMQ connection, acknowledge/reject/retry a delivery, open a
transaction itself, schedule a loop, or change Kubernetes state. The existing
coordinator retains ownership of MinIO/model/short-transaction ordering.
"""

from __future__ import annotations

from pathlib import Path

from app.amqp_manual_ack import BasicPitchConsumeOneOutcome, BasicPitchConsumeOneResult
from app.basic_pitch_process import BasicPitchProcessRunner, DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS
from app.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchTaskExecutionDatabase,
    BasicPitchTaskExecutionStorageClient,
    execute_claimed_basic_pitch_task,
)
from app.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.task_lease import BasicPitchTaskLease


class BasicPitchAcknowledgedLeaseExecutionError(RuntimeError):
    """A normal receive result is not eligible to start worker-side execution.

    This fixed control-flow category is not a retry policy. Idle, duplicate,
    stale, and malformed outcomes intentionally have no current ownership, so
    a future supervisor must handle those paths without treating this error as
    media work or making another broker action.
    """


def execute_acknowledged_basic_pitch_lease(
    receive_result: BasicPitchConsumeOneResult,
    *,
    database: BasicPitchTaskExecutionDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
) -> BasicPitchClaimedTaskExecution:
    """Run the existing coordinator only for one acknowledged current lease.

    The result dataclass enforces the normal evidence pairing. The defensive
    checks below protect this security-sensitive handoff from future unsafe
    object construction or a test double: it must not begin MinIO/model work
    without both the committed lease and the parser-validated message that
    names/checksums the exact private input stem.

    Storage/database/process exceptions intentionally propagate from the
    coordinator unchanged. The future long-running supervisor, not this gate,
    will decide retry timing, lease recovery, and readiness/health behavior.
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

    # This is a pure handoff: the coordinator itself starts with HeadObject,
    # manages temporary scratch lifetime, runs the model, verifies/uploads MIDI,
    # and opens its own short start/completion transactions. No RabbitMQ action
    # can occur here because neither a channel nor delivery tag is accepted.
    return execute_claimed_basic_pitch_task(
        database=database,
        storage_client=storage_client,
        message=message,
        lease=lease,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )
