"""Perform one bounded ADTOF receive-and-execute iteration.

This composition joins two existing, narrow boundaries exactly once:

1. ``amqp_manual_ack.py`` receives at most one request and performs the
   reviewed RabbitMQ acknowledgement/rejection action after PostgreSQL's
   durable first-claim decision.
2. ``acknowledged_lease_execution.py`` starts MinIO/CPU/finalization work only
   for the resulting acknowledged current lease.

There is intentionally no ``while`` loop, sleep/backoff, connection/channel
setup, retry schedule, lease-recovery scan, health server, signal handler,
image entrypoint, or Kubernetes operation here.  Those need independent
supervisor/lifecycle policies.  Keeping one iteration explicit makes the
important safety property testable: an idle, duplicate/stale, or malformed-DLQ
outcome cannot begin ADTOF work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from app.acknowledged_lease_execution import execute_acknowledged_adtof_lease
from app.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.amqp_manual_ack import (
    ADTOFConsumeOneOutcome,
    ADTOFConsumeOneResult,
    consume_one_adtof_requested_delivery,
)
from app.claimed_task_success import (
    ADTOFClaimedTaskSuccess,
    ADTOFClaimedTaskSuccessDatabase,
    ADTOFClaimedTaskSuccessStorageClient,
)
from app.first_claim import ADTOFTaskClaimDatabase


class ADTOFWorkerIterationDatabase(
    ADTOFTaskClaimDatabase,
    ADTOFClaimedTaskSuccessDatabase,
    Protocol,
):
    """Restricted database surface shared across one worker iteration.

    The concrete PostgreSQL adapter exposes one `write_cursor()` method.  The
    manual-ack claim and the post-claim success path each use separate short
    transactions through it; this combined protocol neither creates nor
    authorizes a single long transaction around broker, MinIO, or CPU work.
    """


class ADTOFWorkerIterationOutcome(StrEnum):
    """The normal, non-sensitive outcomes of one bounded iteration."""

    # No RabbitMQ delivery existed; no database/storage/CPU action occurred.
    IDLE = "idle"
    # A duplicate/stale durable fact was acknowledged; it has no current lease.
    ACKNOWLEDGED_NO_WORK = "acknowledged_no_work"
    # A malformed request was rejected into RabbitMQ's configured DLQ route.
    MALFORMED_REJECTED = "malformed_rejected"
    # One acknowledged current lease ran through the post-claim coordinator.
    EXECUTED = "executed"


@dataclass(frozen=True)
class ADTOFWorkerIterationResult:
    """Compact result after one receive-and-optional-execution attempt.

    Raw AMQP data, delivery tags, clients, passwords, scratch paths, object
    keys, and model output are intentionally excluded.  Only an `EXECUTED`
    result carries compact completion/ownership evidence from the existing
    post-claim coordinator.
    """

    outcome: ADTOFWorkerIterationOutcome
    execution: ADTOFClaimedTaskSuccess | None = None

    def __post_init__(self) -> None:
        """Prevent a no-work outcome from being misreported as CPU execution."""

        has_execution = self.execution is not None
        requires_execution = self.outcome is ADTOFWorkerIterationOutcome.EXECUTED
        if has_execution != requires_execution:
            raise ValueError("ADTOF worker iteration execution does not match its outcome.")
        if has_execution and not isinstance(self.execution, ADTOFClaimedTaskSuccess):
            raise ValueError("ADTOF worker iteration execution is invalid.")


def _no_work_result(receive_result: ADTOFConsumeOneResult) -> ADTOFWorkerIterationResult:
    """Map a normal non-executing broker outcome without retaining transport data."""

    mapping = {
        ADTOFConsumeOneOutcome.IDLE: ADTOFWorkerIterationOutcome.IDLE,
        ADTOFConsumeOneOutcome.ACKNOWLEDGED_NO_WORK: ADTOFWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK,
        ADTOFConsumeOneOutcome.MALFORMED_REJECTED: ADTOFWorkerIterationOutcome.MALFORMED_REJECTED,
    }
    try:
        outcome = mapping[receive_result.outcome]
    except KeyError as error:
        raise ValueError("ADTOF receive result is invalid for no-work mapping.") from error
    return ADTOFWorkerIterationResult(outcome=outcome)


def receive_and_execute_adtof_once(
    channel: Any,
    *,
    database: ADTOFWorkerIterationDatabase,
    storage_client: ADTOFClaimedTaskSuccessStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
) -> ADTOFWorkerIterationResult:
    """Run one broker decision and, only when safe, one ADTOF success attempt.

    The manual-ack adapter owns `basic_get`, contract rejection, and durable
    commit-before-ack ordering.  Its known normal outcomes are mapped here;
    all receive or execution failures propagate unchanged so a later
    supervisor can choose retry/backoff/restart behavior.  The channel is not
    passed to the execution gate, preventing a second broker action after the
    acknowledged lease handoff.
    """

    receive_result = consume_one_adtof_requested_delivery(channel, database=database)
    if not isinstance(receive_result, ADTOFConsumeOneResult):
        raise TypeError("ADTOF manual acknowledgement returned an invalid result.")

    if receive_result.outcome is not ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE:
        return _no_work_result(receive_result)

    # The execution gate repeats the outcome/lease/request validation before
    # it gives the coordinator any storage or CPU authority.
    execution = execute_acknowledged_adtof_lease(
        receive_result,
        database=database,
        storage_client=storage_client,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )
    return ADTOFWorkerIterationResult(
        outcome=ADTOFWorkerIterationOutcome.EXECUTED,
        execution=execution,
    )
