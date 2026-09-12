"""Run one fair, bounded Basic Pitch broker/retry work-selection iteration.

The pure round-robin policy selects either one ordinary RabbitMQ delivery
attempt or one due PostgreSQL retry-recovery attempt.  This composition runs
that selected source at most once.  If it is idle, it immediately checks the
other source once; only when *both* are idle does it report an idle iteration
for the existing supervisor backoff policy.

The function is deliberately not a worker loop.  It never sleeps, opens or
closes a broker connection/channel, opens a database connection itself,
catches/classifies errors, renews a lease, starts a Kubernetes Job, or changes
Kubernetes state.  Existing leaf boundaries own their own short database
transactions and MinIO/model ordering.  Any exception escapes unchanged so a
future long-running supervisor can apply its reviewed reconnect/backoff and
shutdown policy without hiding incomplete work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from app.basic_pitch_process import BasicPitchProcessRunner, DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS
from app.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchTaskExecutionDatabase,
    BasicPitchTaskExecutionStorageClient,
)
from app.due_retry_recovery import BasicPitchDueRetryRecoveryDatabase, recover_one_due_basic_pitch_retry
from app.receive_execute_once import (
    BasicPitchWorkerIterationDatabase,
    BasicPitchWorkerIterationOutcome,
    BasicPitchWorkerIterationResult,
    receive_and_execute_basic_pitch_once,
)
from app.recovered_retry_execution import execute_recovered_basic_pitch_retry
from app.work_schedule import (
    BasicPitchWorkScheduleState,
    BasicPitchWorkSource,
    select_next_basic_pitch_work_source,
)


class BasicPitchFairWorkIterationDatabase(
    BasicPitchWorkerIterationDatabase,
    BasicPitchDueRetryRecoveryDatabase,
    Protocol,
):
    """The shared restricted database capability for either selected source.

    Both paths use the same concrete least-privilege Basic Pitch PostgreSQL
    adapter, but each leaf operation opens its own short transaction.  This
    combined Protocol does not grant a long transaction around broker polling,
    recovery, MinIO I/O, or CPU model execution.
    """


class BasicPitchFairWorkIterationOutcome(StrEnum):
    """The only normal facts the future supervisor needs after this iteration."""

    # At least one source had a normal non-idle outcome. It may have completed
    # a model attempt, recorded a retry/terminal result, or safely handled a
    # broker duplicate/malformed message; no idle delay should be added.
    PROGRESS = "progress"
    # Both round-robin sources were checked once and were empty. Only this
    # state qualifies for the supervisor's short interruptible idle delay.
    IDLE = "idle"


@dataclass(frozen=True)
class BasicPitchFairWorkIterationResult:
    """Compact result of one or two fair source attempts.

    ``attempted_sources`` has one entry on immediate progress, or both distinct
    entries when the first source was idle and the other was checked. The
    nested result retains only the existing compact worker/execution evidence,
    never a broker frame, database cursor, object key, credential, or process
    output. An ``IDLE`` result intentionally carries neither nested result.
    """

    outcome: BasicPitchFairWorkIterationOutcome
    next_state: BasicPitchWorkScheduleState
    attempted_sources: tuple[BasicPitchWorkSource, ...]
    broker_result: BasicPitchWorkerIterationResult | None = None
    recovery_execution: BasicPitchClaimedTaskExecution | None = None

    def __post_init__(self) -> None:
        """Keep source/result pairings unambiguous for a future supervisor."""

        if not isinstance(self.outcome, BasicPitchFairWorkIterationOutcome):
            raise TypeError("Basic Pitch fair work iteration outcome is invalid.")
        if not isinstance(self.next_state, BasicPitchWorkScheduleState):
            raise TypeError("Basic Pitch fair work iteration next state is invalid.")
        if (
            not isinstance(self.attempted_sources, tuple)
            or not 1 <= len(self.attempted_sources) <= 2
            or any(not isinstance(source, BasicPitchWorkSource) for source in self.attempted_sources)
            or len(set(self.attempted_sources)) != len(self.attempted_sources)
        ):
            raise TypeError("Basic Pitch fair work iteration sources are invalid.")
        if self.broker_result is not None and not isinstance(
            self.broker_result, BasicPitchWorkerIterationResult
        ):
            raise TypeError("Basic Pitch fair work broker result is invalid.")
        if self.recovery_execution is not None and not isinstance(
            self.recovery_execution, BasicPitchClaimedTaskExecution
        ):
            raise TypeError("Basic Pitch fair work recovery result is invalid.")

        if self.outcome is BasicPitchFairWorkIterationOutcome.IDLE:
            if (
                len(self.attempted_sources) != 2
                or set(self.attempted_sources)
                != {BasicPitchWorkSource.RABBITMQ_DELIVERY, BasicPitchWorkSource.DUE_RETRY_RECOVERY}
                or self.broker_result is not None
                or self.recovery_execution is not None
            ):
                raise ValueError("An idle fair Basic Pitch iteration requires two empty source checks.")
            return

        # A normal broker `IDLE` result never counts as progress. A recovered
        # lease may produce `OWNERSHIP_LOST` but still counts as progress here:
        # it was a due durable candidate, not evidence that both sources were
        # empty, so the supervisor must not sleep before the next fair check.
        if (
            (self.broker_result is None) == (self.recovery_execution is None)
            or (
                self.broker_result is not None
                and (
                    self.broker_result.outcome is BasicPitchWorkerIterationOutcome.IDLE
                    or BasicPitchWorkSource.RABBITMQ_DELIVERY not in self.attempted_sources
                )
            )
            or (
                self.recovery_execution is not None
                and BasicPitchWorkSource.DUE_RETRY_RECOVERY not in self.attempted_sources
            )
        ):
            raise ValueError("A progressing fair Basic Pitch iteration requires one non-idle result.")


def _run_rabbitmq_attempt(
    channel: Any,
    *,
    database: BasicPitchFairWorkIterationDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    work_directory: Path,
    process_timeout_seconds: int,
    process_runner: BasicPitchProcessRunner | None,
) -> BasicPitchWorkerIterationResult | None:
    """Run the normal path once; return ``None`` only for an empty broker queue."""

    result = receive_and_execute_basic_pitch_once(
        channel,
        database=database,
        storage_client=storage_client,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )
    if not isinstance(result, BasicPitchWorkerIterationResult):
        raise TypeError("Basic Pitch RabbitMQ iteration returned an invalid result.")
    if result.outcome is BasicPitchWorkerIterationOutcome.IDLE:
        return None
    return result


def _run_due_retry_attempt(
    *,
    database: BasicPitchFairWorkIterationDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    work_directory: Path,
    process_timeout_seconds: int,
    process_runner: BasicPitchProcessRunner | None,
) -> BasicPitchClaimedTaskExecution | None:
    """Recover and execute one due task; return ``None`` when none is due."""

    recovery = recover_one_due_basic_pitch_retry(database=database)
    if recovery is None:
        return None
    return execute_recovered_basic_pitch_retry(
        recovery,
        database=database,
        storage_client=storage_client,
        work_directory=work_directory,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
    )


def run_one_fair_basic_pitch_work_iteration(
    channel: Any,
    *,
    schedule_state: BasicPitchWorkScheduleState,
    database: BasicPitchFairWorkIterationDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
) -> BasicPitchFairWorkIterationResult:
    """Make one fair source attempt and one immediate idle fallback at most.

    A selected RabbitMQ source delegates to the complete existing
    parse/claim/acknowledge/execute boundary. A selected due-retry source
    delegates to its committed claim/evidence/execution boundary. The first
    source is never retried in this call. If it was idle, the alternate source
    receives exactly one chance; then this returns either its progress or the
    explicit two-source `IDLE` result for a future supervisor to delay.

    No exception is caught. A channel/database/MinIO/model failure must retain
    its original type and escape before this function reports normal progress
    or idle, allowing a later supervisor to decide safe reconnection/backoff.
    """

    if not isinstance(schedule_state, BasicPitchWorkScheduleState):
        raise TypeError("schedule_state must be BasicPitchWorkScheduleState.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    first = select_next_basic_pitch_work_source(schedule_state)
    attempted_sources = [first.source]
    if first.source is BasicPitchWorkSource.RABBITMQ_DELIVERY:
        broker_result = _run_rabbitmq_attempt(
            channel,
            database=database,
            storage_client=storage_client,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
        if broker_result is not None:
            return BasicPitchFairWorkIterationResult(
                outcome=BasicPitchFairWorkIterationOutcome.PROGRESS,
                next_state=first.next_state,
                attempted_sources=tuple(attempted_sources),
                broker_result=broker_result,
            )
    else:
        recovery_execution = _run_due_retry_attempt(
            database=database,
            storage_client=storage_client,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
        if recovery_execution is not None:
            return BasicPitchFairWorkIterationResult(
                outcome=BasicPitchFairWorkIterationOutcome.PROGRESS,
                next_state=first.next_state,
                attempted_sources=tuple(attempted_sources),
                recovery_execution=recovery_execution,
            )

    # The selected source was empty. The schedule advanced at selection time,
    # so this picks the other source exactly once without repolling the idle
    # source or sleeping. The alternation invariants are owned by the policy.
    fallback = select_next_basic_pitch_work_source(first.next_state)
    attempted_sources.append(fallback.source)
    if fallback.source is BasicPitchWorkSource.RABBITMQ_DELIVERY:
        broker_result = _run_rabbitmq_attempt(
            channel,
            database=database,
            storage_client=storage_client,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
        if broker_result is not None:
            return BasicPitchFairWorkIterationResult(
                outcome=BasicPitchFairWorkIterationOutcome.PROGRESS,
                next_state=fallback.next_state,
                attempted_sources=tuple(attempted_sources),
                broker_result=broker_result,
            )
    else:
        recovery_execution = _run_due_retry_attempt(
            database=database,
            storage_client=storage_client,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
        if recovery_execution is not None:
            return BasicPitchFairWorkIterationResult(
                outcome=BasicPitchFairWorkIterationOutcome.PROGRESS,
                next_state=fallback.next_state,
                attempted_sources=tuple(attempted_sources),
                recovery_execution=recovery_execution,
            )

    return BasicPitchFairWorkIterationResult(
        outcome=BasicPitchFairWorkIterationOutcome.IDLE,
        next_state=fallback.next_state,
        attempted_sources=tuple(attempted_sources),
    )
