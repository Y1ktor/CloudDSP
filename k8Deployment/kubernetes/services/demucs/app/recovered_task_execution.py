"""Enter the ordinary Demucs one-task policy from committed recovery evidence.

``task_maintenance.py`` recovers a due retry or expired task without receiving
a new RabbitMQ message.  Its :class:`DemucsRecoveredTask` carries the committed
lease and the strict published-outbox request evidence that replaces that
message.  This narrow gate accepts only that pair and invokes the existing
one-task running-failure policy once.

The established Demucs attempt policy predates recovery and accepts a compact
post-acknowledgement result as its lease carrier.  This module creates that
*data-only compatibility envelope* from the recovery lease: it has no channel,
delivery tag, AMQP body, properties, or acknowledgement method, and it does
not call RabbitMQ.  The recovered pair—not this envelope—is the authority to
process.  Reusing the carrier lets recovery retain the normal source preflight,
guarded ``leased -> running`` start, lease renewal, private stem upload, and
reviewed pre-model/running failure transitions without duplicating them.

This module does not claim/recover/renew a lease itself, write PostgreSQL
directly, receive/publish/acknowledge RabbitMQ, access MinIO or Demucs itself,
sleep, run a loop, or change a Kubernetes resource.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from app.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.demucs_artifact_upload import DemucsPutObjectClient
from app.demucs_process import DemucsProcessRunner
from app.ffprobe_process import DemucsFFprobeRunner
from app.planned_stem_upload import DemucsPlannedStemUploader
from app.pre_model_failure_transition import DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
from app.running_failure_runtime import (
    DemucsOneTaskExecution,
    DemucsRunningFailureRuntimeDatabase,
    execute_acknowledged_demucs_task_with_running_failure_policy,
)
from app.running_failure_transition import DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS
from app.source_preflight import DemucsSourcePreflightClient
from app.task_maintenance import DemucsRecoveredTask


class DemucsRecoveredTaskExecutionError(RuntimeError):
    """The caller did not supply the committed pair required for recovery work."""


def _post_acknowledgement_compatibility_result(
    recovery: DemucsRecoveredTask,
) -> DemucsConsumeOneResult:
    """Adapt a committed recovery lease to the existing non-transport runtime.

    ``DemucsConsumeOneResult`` contains only an outcome and lease; constructing
    it performs no broker operation. Its ``ACKNOWLEDGED_LEASE`` value means the
    downstream attempt path may process an already durable, post-broker task.
    For recovery, that historic broker decision has no new frame at this time;
    the immutable `DemucsRecoveredTask` is independently validated first and
    remains the sole source of authorization.
    """

    if not isinstance(recovery, DemucsRecoveredTask):
        raise DemucsRecoveredTaskExecutionError(
            "Demucs recovery execution requires committed recovery evidence."
        )
    return DemucsConsumeOneResult(
        outcome=DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE,
        lease=recovery.lease,
    )


def execute_recovered_demucs_task(
    recovery: DemucsRecoveredTask,
    *,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    database: DemucsRunningFailureRuntimeDatabase,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
    pre_model_retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    running_retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
) -> DemucsOneTaskExecution:
    """Run one committed recovered task through the ordinary failure-safe path.

    The frozen pair proves this is a second/third attempt whose lease, event,
    job, source bucket/key, and stem mode agree. The reused runtime nevertheless
    repeats MinIO/FFprobe checks and must still commit its guarded
    ``leased -> running`` transition before a model starts. Its normal success,
    ownership loss, retry, and terminal outcomes are returned unchanged.

    There is intentionally no `basic_ack` here. The initial request's broker
    lifecycle was already decided before recovery; PostgreSQL retry scheduling
    and the committed recovery pair now carry the durable work handoff.
    """

    compatibility_result = _post_acknowledgement_compatibility_result(recovery)
    return execute_acknowledged_demucs_task_with_running_failure_policy(
        receive_result=compatibility_result,
        source_client=source_client,
        artifact_client=artifact_client,
        database=database,
        work_directory=work_directory,
        ffprobe_runner=ffprobe_runner,
        demucs_runner=demucs_runner,
        uploader=uploader,
        event_id_factory=event_id_factory,
        pre_model_retry_after_seconds=pre_model_retry_after_seconds,
        running_retry_after_seconds=running_retry_after_seconds,
    )
