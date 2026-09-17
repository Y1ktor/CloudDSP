"""Run at most one delivery-free ADTOF expired-lease recovery attempt.

This module is the recovery analogue of ``receive_execute_once.py``. It has no
RabbitMQ channel because an expired task's original message was acknowledged
before its owning Pod stopped. Instead, it first asks the short PostgreSQL
recovery transaction for one committed lease/request pair. Only that complete
pair may enter the recovered-task gate and the already-reviewed post-claim
success path.

There is intentionally no scan loop, sleep, backoff, broker action, signal
handling, Deployment, or Kubernetes API call here. The separate cadence-driven
worker cycle decides when this bounded attempt runs among normal AMQP
iterations, preventing busy incoming traffic from starving crash recovery
without adding an implicit second worker loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from app.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.claimed_task_success import (
    ADTOFClaimedTaskSuccess,
    ADTOFClaimedTaskSuccessDatabase,
    ADTOFClaimedTaskSuccessStorageClient,
)
from app.recovered_task_execution import execute_recovered_adtof_task
from app.recovery import (
    ADTOFRecoveredTask,
    ADTOFRecoveryDatabase,
    recover_one_expired_adtof_task,
    terminalize_one_expired_exhausted_adtof_task,
)
from app.task_claim import ADTOFExpiredLeaseTerminalization


class ADTOFRecoveryIterationDatabase(
    ADTOFRecoveryDatabase,
    ADTOFClaimedTaskSuccessDatabase,
    Protocol,
):
    """Restricted database surface used by one recovery scan and execution.

    The recovery claim/read occupies one short transaction. Any later guarded
    start/completion transaction belongs to the existing success coordinator;
    this combined protocol never turns PostgreSQL, MinIO, or CPU work into one
    long transaction.
    """


class ADTOFRecoveryIterationOutcome(StrEnum):
    """The normal durable outcomes of one delivery-free recovery attempt."""

    # The recovery query found no safe expired active lease.
    IDLE = "idle"
    # An expired third attempt was durably marked failed; it must not receive
    # an impossible fourth lease or enter the model-execution gate.
    TERMINALIZED = "terminalized"
    # One committed recovery pair reached the standard post-claim coordinator.
    EXECUTED = "executed"


@dataclass(frozen=True)
class ADTOFRecoveryIterationResult:
    """Compact result that keeps recovery scheduling separate from execution.

    The result intentionally retains no lease token, outbox payload, object
    key, scratch path, storage response, or RabbitMQ information. An
    `EXECUTED` result includes the coordinator's compact completion or
    ownership-loss fact, while `TERMINALIZED` includes only the durable
    third-attempt task failure evidence.
    """

    outcome: ADTOFRecoveryIterationOutcome
    execution: ADTOFClaimedTaskSuccess | None = None
    terminalization: ADTOFExpiredLeaseTerminalization | None = None

    def __post_init__(self) -> None:
        """Prevent idle scans from being represented as successful CPU work."""

        has_execution = self.execution is not None
        has_terminalization = self.terminalization is not None
        if has_execution and has_terminalization:
            raise ValueError("ADTOF recovery iteration cannot execute and terminalize together.")
        if (self.outcome is ADTOFRecoveryIterationOutcome.EXECUTED) != has_execution:
            raise ValueError("ADTOF recovery iteration execution does not match its outcome.")
        if (self.outcome is ADTOFRecoveryIterationOutcome.TERMINALIZED) != has_terminalization:
            raise ValueError("ADTOF recovery iteration terminalization does not match its outcome.")
        if self.outcome is ADTOFRecoveryIterationOutcome.IDLE and (has_execution or has_terminalization):
            raise ValueError("ADTOF idle recovery iteration cannot retain work evidence.")
        if has_execution and not isinstance(self.execution, ADTOFClaimedTaskSuccess):
            raise ValueError("ADTOF recovery iteration execution is invalid.")
        if has_terminalization and not isinstance(
            self.terminalization, ADTOFExpiredLeaseTerminalization
        ):
            raise ValueError("ADTOF recovery iteration terminalization is invalid.")


def recover_and_execute_adtof_once(
    *,
    database: ADTOFRecoveryIterationDatabase,
    storage_client: ADTOFClaimedTaskSuccessStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
) -> ADTOFRecoveryIterationResult:
    """Make one terminal/recovery decision, execute at most one task, or idle.

    PostgreSQL claim/read failures and success-path operational failures are
    deliberately not mapped to idle: a later supervisor must classify them and
    choose bounded retry or process restart behavior. This composition makes
    no RabbitMQ action and does not inspect normal broker traffic.
    """

    # A third expired lease is terminal durable work, not recoverable CPU work.
    # Perform this small transition first so the following recovery claim can
    # remain strictly limited to attempts one and two.
    terminalization = terminalize_one_expired_exhausted_adtof_task(database=database)
    if terminalization is not None:
        return ADTOFRecoveryIterationResult(
            outcome=ADTOFRecoveryIterationOutcome.TERMINALIZED,
            terminalization=terminalization,
        )

    recovered_task = recover_one_expired_adtof_task(database=database)
    if recovered_task is None:
        return ADTOFRecoveryIterationResult(outcome=ADTOFRecoveryIterationOutcome.IDLE)
    if not isinstance(recovered_task, ADTOFRecoveredTask):
        raise TypeError("ADTOF recovery returned an invalid result.")

    # The gate repeats recovery-pair validation before it provides MinIO or CPU
    # authority, leaving this composition unable to widen task/object scope.
    execution = execute_recovered_adtof_task(
        recovered_task,
        database=database,
        storage_client=storage_client,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )
    return ADTOFRecoveryIterationResult(
        outcome=ADTOFRecoveryIterationOutcome.EXECUTED,
        execution=execution,
    )
