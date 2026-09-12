"""Execute one bounded Basic Pitch worker iteration without a supervisor loop.

This composition joins the two existing post-topology boundaries exactly once:

1. ``amqp_manual_ack.py`` receives at most one request and makes the reviewed
   RabbitMQ acknowledgement/rejection decision after the durable first claim.
2. ``acknowledged_lease_execution.py`` invokes the existing MinIO/model/result
   coordinator only for an ``ACKNOWLEDGED_LEASE`` result.

It deliberately contains no ``while`` loop, sleep, connection lifecycle,
channel setup, retry schedule, lease-recovery scan, health endpoint, signal
handler, image entrypoint, or Kubernetes action. Those are future supervisor
concerns. Keeping one iteration pure in control flow lets tests show that idle,
duplicate/stale, and malformed outcomes cannot accidentally begin audio work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from app.acknowledged_lease_execution import execute_acknowledged_basic_pitch_lease
from app.amqp_manual_ack import (
    BasicPitchConsumeOneOutcome,
    BasicPitchConsumeOneResult,
    consume_one_basic_pitch_requested_delivery,
)
from app.basic_pitch_process import BasicPitchProcessRunner, DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS
from app.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchTaskExecutionDatabase,
    BasicPitchTaskExecutionStorageClient,
)
from app.first_claim import BasicPitchTaskClaimDatabase


class BasicPitchWorkerIterationDatabase(
    BasicPitchTaskClaimDatabase,
    BasicPitchTaskExecutionDatabase,
    Protocol,
):
    """The shared restricted database surface used across one worker iteration.

    The concrete Basic Pitch PostgreSQL adapter already provides this common
    ``write_cursor()`` capability. The manual-ack claim and later execution
    coordinator still open separate short transactions; this protocol does not
    authorize or create one long transaction around the iteration.
    """


class BasicPitchWorkerIterationOutcome(StrEnum):
    """The normal, transport-safe results from one receive-and-execute attempt."""

    # No broker delivery existed, so no database/storage/model action occurred.
    IDLE = "idle"
    # PostgreSQL classified durable history and RabbitMQ accepted its ack.
    ACKNOWLEDGED_NO_WORK = "acknowledged_no_work"
    # RabbitMQ accepted permanent malformed-message rejection into its DLQ path.
    MALFORMED_REJECTED = "malformed_rejected"
    # An acknowledged current lease completed one coordinator attempt. Its
    # nested result differentiates durable MIDI success from ownership loss.
    EXECUTED = "executed"


@dataclass(frozen=True)
class BasicPitchWorkerIterationResult:
    """Non-sensitive result of one bounded worker iteration.

    The result intentionally omits raw AMQP body/properties, delivery tags,
    broker/database clients, object paths, credentials, and local process
    output. Only an `EXECUTED` iteration retains the coordinator's compact
    durable-result evidence; all other outcomes must have no execution result.
    """

    outcome: BasicPitchWorkerIterationOutcome
    execution: BasicPitchClaimedTaskExecution | None = None

    def __post_init__(self) -> None:
        """Keep no-work results from being mistaken for model execution."""

        has_execution = self.execution is not None
        requires_execution = self.outcome is BasicPitchWorkerIterationOutcome.EXECUTED
        if has_execution != requires_execution:
            raise ValueError("Basic Pitch worker iteration execution does not match its outcome.")
        if has_execution and not isinstance(self.execution, BasicPitchClaimedTaskExecution):
            raise ValueError("Basic Pitch worker iteration execution is invalid.")


def _no_work_result(receive_result: BasicPitchConsumeOneResult) -> BasicPitchWorkerIterationResult:
    """Map a normal non-execution broker outcome without exposing transport data."""

    mapping = {
        BasicPitchConsumeOneOutcome.IDLE: BasicPitchWorkerIterationOutcome.IDLE,
        BasicPitchConsumeOneOutcome.ACKNOWLEDGED_NO_WORK: BasicPitchWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK,
        BasicPitchConsumeOneOutcome.MALFORMED_REJECTED: BasicPitchWorkerIterationOutcome.MALFORMED_REJECTED,
    }
    try:
        outcome = mapping[receive_result.outcome]
    except KeyError as error:
        raise ValueError("Basic Pitch receive result is invalid for no-work mapping.") from error
    return BasicPitchWorkerIterationResult(outcome=outcome)


def receive_and_execute_basic_pitch_once(
    channel: Any,
    *,
    database: BasicPitchWorkerIterationDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
) -> BasicPitchWorkerIterationResult:
    """Perform exactly one receive decision and optional Basic Pitch execution.

    The manual-ack adapter owns `basic_get`, contract rejection, and commit-
    before-ack ordering. This function never reinterprets malformed/transient
    errors: they propagate before a result exists. For a returned normal result,
    only ``ACKNOWLEDGED_LEASE`` enters the post-ack gate; idle, acknowledged
    duplicate/stale history, and DLQ rejection return immediately without a
    MinIO request, temporary file, database transaction, or model process.
    """

    receive_result = consume_one_basic_pitch_requested_delivery(channel, database=database)
    if not isinstance(receive_result, BasicPitchConsumeOneResult):
        raise TypeError("Basic Pitch manual acknowledgement returned an invalid result.")

    if receive_result.outcome is not BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE:
        return _no_work_result(receive_result)

    # The gate repeats the strict outcome/lease/message checks before it gives
    # the coordinator storage/model authority. The channel is intentionally not
    # passed onward, so no second acknowledgement action can happen after this
    # point in the iteration.
    execution = execute_acknowledged_basic_pitch_lease(
        receive_result,
        database=database,
        storage_client=storage_client,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )
    return BasicPitchWorkerIterationResult(
        outcome=BasicPitchWorkerIterationOutcome.EXECUTED,
        execution=execution,
    )
