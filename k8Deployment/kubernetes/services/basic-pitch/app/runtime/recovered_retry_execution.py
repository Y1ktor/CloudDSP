"""Enter the normal Basic Pitch execution policy from a committed retry pair.

``due_retry_recovery.py`` atomically commits a fresh due-retry lease and its
strict request reconstructed from the published outbox event.  This small gate
accepts only that paired result and invokes the existing post-lease execution
policy once.  It therefore reuses the ordinary MinIO preflight, guarded model
start, MIDI completion, permanent input failure, and transient storage retry
rules without manufacturing a second RabbitMQ delivery.

It does not claim/renew a lease, read/write PostgreSQL directly, receive or
publish/acknowledge RabbitMQ, call MinIO or Basic Pitch itself, sleep, run a
loop, or create Kubernetes resources.  Those capabilities remain in the
separate committed recovery, execution, and future supervisor layers.
"""

from __future__ import annotations

from pathlib import Path

from app.runtime.acknowledged_lease_execution import execute_current_basic_pitch_lease
from app.processing.basic_pitch_process import BasicPitchProcessRunner, DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS
from app.runtime.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchTaskExecutionDatabase,
    BasicPitchTaskExecutionStorageClient,
)
from app.db.due_retry_recovery import BasicPitchDueRetryRecovery


class BasicPitchRecoveredRetryExecutionError(RuntimeError):
    """The caller did not supply the one committed due-retry evidence pair."""


def execute_recovered_basic_pitch_retry(
    recovery: BasicPitchDueRetryRecovery,
    *,
    database: BasicPitchTaskExecutionDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
) -> BasicPitchClaimedTaskExecution:
    """Run one committed recovered retry through the shared post-lease policy.

    The frozen recovery type has already confirmed that its attempt is a
    retry, and that lease/job/stem/event/input coordinates match the strict
    request. Requiring it here prevents an old broker body or hand-selected
    task from entering the recovery path. The shared policy then revalidates
    at its I/O boundaries and still needs its own guarded `leased -> running`
    transition before the model starts.

    There is intentionally no RabbitMQ acknowledgement here: the first
    delivery was acknowledged before its initial attempt, and durable retry
    scheduling/recovery now belongs to PostgreSQL. A permanent input mismatch
    or temporary MinIO outage receives exactly the same reviewed task outcome
    as the ordinary acknowledged-delivery route.
    """

    if not isinstance(recovery, BasicPitchDueRetryRecovery):
        raise BasicPitchRecoveredRetryExecutionError(
            "Basic Pitch retry execution requires committed recovery evidence."
        )
    return execute_current_basic_pitch_lease(
        database=database,
        storage_client=storage_client,
        message=recovery.message,
        lease=recovery.lease,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )
