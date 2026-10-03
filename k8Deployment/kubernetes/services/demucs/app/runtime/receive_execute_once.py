"""Receive at most one Demucs request and run at most one safe task attempt.

This is the bounded normal-work composition that connects the two completed
layers in their required order:

1. ``amqp_manual_ack.py`` receives one message and acknowledges it only after
   PostgreSQL durably creates a lease (or records duplicate/stale no-work).
2. ``running_failure_runtime.py`` may access MinIO, FFprobe, Demucs, local
   scratch, and PostgreSQL only for that acknowledged current lease. It turns
   reviewed source or post-start disruptions into an already-committed outcome.

An idle queue, an acknowledged duplicate/stale message, and a malformed
message that RabbitMQ accepted into the configured DLQ never reach storage or
CPU work. The channel is intentionally not passed to the attempt policy, so no
second broker acknowledgement can occur after ownership leaves the transport
boundary.

This module is not a persistent consumer. It does not loop, sleep, back off,
recover due/expired leases, itself schedule renewal, create/reconnect/close a
client, install signal handling, build an image, change a Deployment, or modify
KEDA. The nested one-attempt runtime owns short renewal checkpoints while a
model child runs; all other lifecycle concerns need separate reviewed layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from app.messaging.amqp_manual_ack import (
    DemucsConsumeOneOutcome,
    DemucsConsumeOneResult,
    consume_one_demucs_requested_delivery,
)
from app.artifacts.demucs_artifact_upload import DemucsPutObjectClient
from app.processing.demucs_process import DemucsProcessRunner
from app.processing.ffprobe_process import DemucsFFprobeRunner
from app.db.first_claim import DemucsTaskClaimDatabase
from app.artifacts.planned_stem_upload import DemucsPlannedStemUploader
from app.db.pre_model_failure_transition import DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
from app.runtime.running_failure_runtime import (
    DemucsOneTaskExecution,
    DemucsRunningFailureRuntimeDatabase,
    execute_acknowledged_demucs_task_with_running_failure_policy,
)
from app.db.running_failure_transition import DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS
from app.processing.source_preflight import DemucsSourcePreflightClient


class DemucsWorkerIterationDatabase(
    DemucsTaskClaimDatabase,
    DemucsRunningFailureRuntimeDatabase,
    Protocol,
):
    """The restricted database surface used by one normal receive iteration.

    The concrete Psycopg adapter structurally provides the same short
    ``write_cursor()`` primitive to both parents. The first claim and the
    later task transitions each create their own transaction; this combined
    protocol never authorizes a transaction spanning RabbitMQ, object storage,
    FFprobe, CPU work, or private-artifact upload.
    """


class DemucsWorkerIterationOutcome(StrEnum):
    """The normal transport-safe facts a future supervisor can consume."""

    # No delivery arrived, so no claim, storage, or CPU action occurred.
    IDLE = "idle"
    # Durable duplicate/stale history was acknowledged; it has no new lease.
    ACKNOWLEDGED_NO_WORK = "acknowledged_no_work"
    # A permanently invalid message was rejected to the configured DLQ.
    MALFORMED_REJECTED = "malformed_rejected"
    # One acknowledged current lease ran the complete bounded attempt policy.
    EXECUTED = "executed"


@dataclass(frozen=True)
class DemucsWorkerIterationResult:
    """Compact evidence from one receive-and-optional-execution iteration.

    It excludes the RabbitMQ body/properties/tag, lease token, object keys,
    credentials, scratch paths, clients, and exception details. Only the
    executed form retains the existing compact, durable-safe task outcome.
    This makes an idle or broker-only result impossible to misreport as model
    work to a later supervisor or health/metrics boundary.
    """

    outcome: DemucsWorkerIterationOutcome
    execution: DemucsOneTaskExecution | None = None

    def __post_init__(self) -> None:
        """Require execution evidence exactly when this iteration ran a lease."""

        if not isinstance(self.outcome, DemucsWorkerIterationOutcome):
            raise TypeError("Demucs worker iteration outcome is invalid.")
        has_execution = self.execution is not None
        requires_execution = self.outcome is DemucsWorkerIterationOutcome.EXECUTED
        if has_execution != requires_execution:
            raise ValueError("Demucs worker iteration execution does not match its outcome.")
        if has_execution and not isinstance(self.execution, DemucsOneTaskExecution):
            raise TypeError("Demucs worker iteration execution is invalid.")


def _no_work_result(receive_result: DemucsConsumeOneResult) -> DemucsWorkerIterationResult:
    """Map normal non-executing broker outcomes without retaining broker data."""

    mapping = {
        DemucsConsumeOneOutcome.IDLE: DemucsWorkerIterationOutcome.IDLE,
        DemucsConsumeOneOutcome.ACKNOWLEDGED_NO_WORK: DemucsWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK,
        DemucsConsumeOneOutcome.MALFORMED_REJECTED: DemucsWorkerIterationOutcome.MALFORMED_REJECTED,
    }
    try:
        outcome = mapping[receive_result.outcome]
    except KeyError as error:
        # A forged/manual-ack result must not become an invented no-work state
        # after bypassing the sole outcome that authorizes task execution.
        raise ValueError("Demucs receive result is invalid for no-work mapping.") from error
    return DemucsWorkerIterationResult(outcome=outcome)


def receive_and_execute_demucs_once(
    channel: Any,
    *,
    database: DemucsWorkerIterationDatabase,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
    pre_model_retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    running_retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
) -> DemucsWorkerIterationResult:
    """Run one broker decision and optional fully-bounded Demucs attempt.

    The manual-ack adapter owns receive, malformed-message rejection, and the
    PostgreSQL-commit-before-ack order. Its normal non-lease outcomes return
    immediately. Receive errors and errors that the one-attempt runtime leaves
    unclassified propagate unchanged—only a later supervisor may choose a
    reconnect, backoff, process restart, or operator-visible exit policy.

    For an acknowledged lease, the completed runtime owns all task-state
    decisions. It may return success, ownership loss, a durable pre-model or
    running retry, or terminal failure. This composition does not examine that
    nested evidence, create a new broker action, or convert it into timing.
    """

    receive_result = consume_one_demucs_requested_delivery(channel, database=database)
    if not isinstance(receive_result, DemucsConsumeOneResult):
        raise TypeError("Demucs manual acknowledgement returned an invalid result.")

    if receive_result.outcome is not DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE:
        return _no_work_result(receive_result)

    # The runtime repeats the acknowledged-result/lease validation before it
    # opens source work. Do not pass `channel`: it prevents a later code change
    # from acknowledging, rejecting, or publishing from the processing phase.
    execution = execute_acknowledged_demucs_task_with_running_failure_policy(
        receive_result=receive_result,
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
    return DemucsWorkerIterationResult(
        outcome=DemucsWorkerIterationOutcome.EXECUTED,
        execution=execution,
    )
