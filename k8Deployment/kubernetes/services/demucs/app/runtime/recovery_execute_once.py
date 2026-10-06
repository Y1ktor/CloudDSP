"""Run at most one delivery-free Demucs recovery decision and attempt.

This is the recovery counterpart to ``receive_execute_once.py``. A recovered
task has no new RabbitMQ frame: its original request already created durable
work, and PostgreSQL now supplies the lease/request evidence. One iteration
therefore first terminalizes an overdue run or expired third attempt, then—only when there was
no terminalization—claims and executes at most one committed recovery pair.

The composition deliberately has no AMQP channel, polling loop, sleep, backoff,
signal handler, connection lifecycle, image, Deployment, KEDA, or Kubernetes
API action. A later supervisor decides when to interleave this bounded recovery
step with its ordinary RabbitMQ receive iteration, so busy incoming traffic
cannot silently starve durable crash/retry recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.artifacts.demucs_artifact_upload import DemucsPutObjectClient
from app.processing.demucs_process import DemucsProcessRunner
from app.processing.ffprobe_process import DemucsFFprobeRunner
from app.artifacts.planned_stem_upload import DemucsPlannedStemUploader
from app.runtime.pre_model_failure_runtime import DemucsOneTaskExecution
from app.db.pre_model_failure_transition import DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
from app.runtime.recovered_task_execution import execute_recovered_demucs_task
from app.runtime.running_failure_runtime import DemucsRunningFailureRuntimeDatabase
from app.db.running_failure_transition import DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS
from app.processing.source_preflight import DemucsSourcePreflightClient
from app.db.task_lease import DemucsExpiredLeaseTerminalization
from app.db.task_maintenance import (
    DemucsRecoveredTask,
    DemucsTaskMaintenanceDatabase,
    recover_one_demucs_task,
    terminalize_one_expired_exhausted_demucs_task,
)


class DemucsRecoveryIterationDatabase(
    DemucsTaskMaintenanceDatabase,
    DemucsRunningFailureRuntimeDatabase,
    Protocol,
):
    """The restricted database surface needed for one bounded recovery step.

    Terminalization and recovery claim/read each own their own short write
    transaction. The reused attempt policy opens later start/renewal/result
    transactions independently. Combining the structural protocols here never
    permits a transaction to remain open across MinIO, FFprobe, CPU inference,
    private artifact upload, or RabbitMQ.
    """


class DemucsRecoveryIterationOutcome(StrEnum):
    """The only durable-safe outcomes a later supervisor may schedule around."""

    # Neither finalization nor recovery found eligible task state.
    IDLE = "idle"
    # One expired final attempt and its Job were durably failed; no model ran.
    TERMINALIZED = "terminalized"
    # One committed recovery pair reached the ordinary one-task policy.
    EXECUTED = "executed"


@dataclass(frozen=True)
class DemucsRecoveryIterationResult:
    """Compact evidence for one terminal/recovery decision without secrets.

    The result carries no task token, event/outbox payload, source key,
    temporary workspace, storage/client object, broker data, or exception.
    An execution result already contains only its established compact durable
    outcome, while a terminal result retains only task/Job finalization proof.
    """

    outcome: DemucsRecoveryIterationOutcome
    execution: DemucsOneTaskExecution | None = None
    terminalization: DemucsExpiredLeaseTerminalization | None = None

    def __post_init__(self) -> None:
        """Require exactly the evidence allowed for the declared outcome."""

        if not isinstance(self.outcome, DemucsRecoveryIterationOutcome):
            raise TypeError("Demucs recovery iteration outcome is invalid.")
        has_execution = self.execution is not None
        has_terminalization = self.terminalization is not None
        if has_execution and has_terminalization:
            raise ValueError("Demucs recovery iteration cannot execute and terminalize together.")
        if (self.outcome is DemucsRecoveryIterationOutcome.EXECUTED) != has_execution:
            raise ValueError("Demucs recovery iteration execution does not match its outcome.")
        if (self.outcome is DemucsRecoveryIterationOutcome.TERMINALIZED) != has_terminalization:
            raise ValueError("Demucs recovery iteration terminalization does not match its outcome.")
        if self.outcome is DemucsRecoveryIterationOutcome.IDLE and (has_execution or has_terminalization):
            raise ValueError("Demucs idle recovery iteration cannot retain work evidence.")
        if has_execution and not isinstance(self.execution, DemucsOneTaskExecution):
            raise TypeError("Demucs recovery iteration execution is invalid.")
        if has_terminalization and not isinstance(
            self.terminalization,
            DemucsExpiredLeaseTerminalization,
        ):
            raise TypeError("Demucs recovery iteration terminalization is invalid.")


def recover_and_execute_demucs_once(
    *,
    database: DemucsRecoveryIterationDatabase,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
    pre_model_retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    running_retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
) -> DemucsRecoveryIterationResult:
    """Terminalize first, otherwise attempt one recovered task, otherwise idle.

    Database recovery/terminalization failures and unclassified operational
    errors intentionally propagate. They are not idle: a later supervisor must
    apply explicit outage/backoff/process policy. This function creates no
    RabbitMQ action; the recovered execution gate receives only a validated
    durable pair and preserves the normal task runtime's failure categories.
    """

    # An overdue run or final expired lease is durable terminal work. It
    # always takes priority over recovery, so a timed-out request cannot enter
    # another model attempt and attempt three cannot gain a fourth token.
    terminalization = terminalize_one_expired_exhausted_demucs_task(database=database)
    if terminalization is not None:
        return DemucsRecoveryIterationResult(
            outcome=DemucsRecoveryIterationOutcome.TERMINALIZED,
            terminalization=terminalization,
        )

    recovered_task = recover_one_demucs_task(database=database)
    if recovered_task is None:
        return DemucsRecoveryIterationResult(outcome=DemucsRecoveryIterationOutcome.IDLE)
    if not isinstance(recovered_task, DemucsRecoveredTask):
        raise TypeError("Demucs recovery returned an invalid result.")

    # The gate repeats pair validation before it exposes any storage/model
    # capability. No AMQP channel is accepted or passed across this boundary.
    execution = execute_recovered_demucs_task(
        recovered_task,
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
    return DemucsRecoveryIterationResult(
        outcome=DemucsRecoveryIterationOutcome.EXECUTED,
        execution=execution,
    )
