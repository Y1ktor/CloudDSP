"""Run the Demucs supervisor with short-lived, reconnectable AMQP sessions.

The worker polls one RabbitMQ delivery with ``basic_get`` rather than keeping a
server-side consumer registration. A Pika channel that becomes stale after a
broker/network interruption must never survive indefinitely behind an idle
``Event.wait``: the next normal polling turn must create a new, restricted
connection and passively re-check the queue before it can receive work.

This module owns *AMQP session lifetime only*. A recovery turn has no RabbitMQ
delivery and opens no AMQP socket. A normal turn opens one preconfigured
session, runs exactly one existing supervisor cycle, then closes the channel
and connection before the next recovery turn. PostgreSQL task leases, MinIO
artifacts, message acknowledgement, model execution, and Kubernetes lifecycle
all remain in their existing narrow layers.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.amqp_channel import DemucsAMQPChannelUnavailable
from app.amqp_connection import DemucsAMQPConnectionUnavailable
from app.amqp_manual_ack import DemucsAMQPUnavailable
from app.amqp_session import opened_demucs_rabbitmq_session
from app.demucs_artifact_upload import DemucsPutObjectClient
from app.demucs_process import DemucsProcessRunner
from app.ffprobe_process import DemucsFFprobeRunner
from app.planned_stem_upload import DemucsPlannedStemUploader
from app.pre_model_failure_transition import DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
from app.recovery_cadence import DemucsWorkerCadenceAction
from app.running_failure_transition import DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS
from app.shutdown_event import DemucsShutdownWaiter
from app.source_preflight import DemucsSourcePreflightClient
from app.supervisor_action import DemucsSupervisorActionOutcome, apply_demucs_supervisor_decision
from app.supervisor_backoff import DemucsSupervisorEvent, next_demucs_supervisor_decision
from app.supervisor_failure_classification import classify_demucs_supervisor_failure
from app.supervisor_loop import (
    DemucsSupervisorLoopOutcome,
    DemucsSupervisorLoopResult,
    _shutdown_requested_before_cycle,
)
from app.supervisor_once import DemucsSupervisorOnceResult, run_one_demucs_supervisor_cycle
from app.supervisor_step import DemucsSupervisorStepResult, DemucsSupervisorStepState
from app.worker_cycle import DemucsWorkerCycleDatabase


# A recovery cycle does not call any channel method. Keeping this sentinel
# private makes that fact explicit without creating a fake Pika channel or
# accidentally granting a recovery scan an AMQP capability.
_RECOVERY_ONLY_CHANNEL = object()

# Only these sanitized session-layer failures are eligible for local reconnect
# backoff. Unknown exceptions still escape to Kubernetes instead of being
# hidden as a harmless empty queue poll.
_RETRYABLE_AMQP_SESSION_ERRORS = (
    DemucsAMQPConnectionUnavailable,
    DemucsAMQPChannelUnavailable,
    DemucsAMQPUnavailable,
)

DemucsAMQPSessionFactory = Callable[[], AbstractContextManager[Any]]


def _retryable_session_cycle(
    *,
    state: DemucsSupervisorStepState,
    shutdown_waiter: DemucsShutdownWaiter,
    error: BaseException,
    jitter_fraction: float,
) -> DemucsSupervisorOnceResult:
    """Turn one reviewed session failure into a bounded reconnect decision.

    No normal receive completed when a session could not open or close, so the
    cadence retains its current normal-AMQP action. The existing supervisor
    backoff supplies a finite interruptible delay. This is not a durable task
    retry and never acknowledges, requeues, or mutates application work.
    """

    event = classify_demucs_supervisor_failure(error)
    if event is not DemucsSupervisorEvent.RETRYABLE_FAILURE:
        # The caller already narrows errors to the reviewed tuple. Retain this
        # guard so a future classifier edit cannot label an unknown failure as
        # a safe reconnect without an explicit code review.
        raise TypeError("Demucs AMQP session failure classification is invalid.")

    decision = next_demucs_supervisor_decision(
        state.backoff_state,
        event,
        jitter_fraction=jitter_fraction,
    )
    next_state = DemucsSupervisorStepState(
        backoff_state=decision.next_state,
        # Preserve the selected normal action. The next turn must obtain a new
        # session rather than attempt work through the failed channel.
        cadence_state=state.cadence_state,
    )
    step = DemucsSupervisorStepResult(
        event=event,
        decision=decision,
        next_state=next_state,
    )
    action_result = apply_demucs_supervisor_decision(
        decision,
        shutdown_waiter=shutdown_waiter,
    )
    return DemucsSupervisorOnceResult(
        step=step,
        action_result=action_result,
        next_state=next_state,
    )


def _run_one_recovery_or_normal_cycle(
    *,
    state: DemucsSupervisorStepState,
    database: DemucsWorkerCycleDatabase,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    work_directory: Path,
    shutdown_waiter: DemucsShutdownWaiter,
    session_factory: DemucsAMQPSessionFactory,
    ffprobe_runner: DemucsFFprobeRunner | None,
    demucs_runner: DemucsProcessRunner | None,
    uploader: DemucsPlannedStemUploader | None,
    event_id_factory: Callable[[], UUID],
    pre_model_retry_after_seconds: int,
    running_retry_after_seconds: int,
    jitter_fraction: float,
) -> DemucsSupervisorOnceResult:
    """Run exactly one cadence-selected cycle, opening AMQP only when needed."""

    common_arguments = {
        "state": state,
        "database": database,
        "source_client": source_client,
        "artifact_client": artifact_client,
        "work_directory": work_directory,
        "shutdown_waiter": shutdown_waiter,
        "ffprobe_runner": ffprobe_runner,
        "demucs_runner": demucs_runner,
        "uploader": uploader,
        "event_id_factory": event_id_factory,
        "pre_model_retry_after_seconds": pre_model_retry_after_seconds,
        "running_retry_after_seconds": running_retry_after_seconds,
        "jitter_fraction": jitter_fraction,
    }

    if state.cadence_state.next_action is DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN:
        # Recovery obtains task truth from PostgreSQL only. The sentinel proves
        # it cannot receive from or acknowledge RabbitMQ during this branch.
        return run_one_demucs_supervisor_cycle(_RECOVERY_ONLY_CHANNEL, **common_arguments)

    if state.cadence_state.next_action is DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION:
        try:
            # The session applies prefetch=1 and passively verifies the fixed
            # queue before `basic_get` can run. It closes after this bounded
            # normal cycle, so the next poll cannot inherit a stale socket.
            with session_factory() as channel:
                return run_one_demucs_supervisor_cycle(channel, **common_arguments)
        except _RETRYABLE_AMQP_SESSION_ERRORS as error:
            return _retryable_session_cycle(
                state=state,
                shutdown_waiter=shutdown_waiter,
                error=error,
                jitter_fraction=jitter_fraction,
            )

    raise TypeError("Demucs supervisor cadence action is invalid.")


def run_demucs_session_supervisor_until_stop(
    *,
    initial_state: DemucsSupervisorStepState,
    database: DemucsWorkerCycleDatabase,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    work_directory: Path,
    shutdown_waiter: DemucsShutdownWaiter,
    session_factory: DemucsAMQPSessionFactory = opened_demucs_rabbitmq_session,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
    pre_model_retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    running_retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
    jitter_fraction: float = 0.0,
) -> DemucsSupervisorLoopResult:
    """Repeat recovery/normal turns while recreating AMQP for normal polling.

    The existing one-cycle runner still owns all task execution and its
    shutdown-aware action wait. This wrapper only chooses whether that one
    cycle needs an AMQP session. An idle laptop worker does not retain a stale
    broker channel, while a real normal turn cannot read a message until a new
    connection, prefetch limit, and passive queue check succeed.
    """

    if not isinstance(initial_state, DemucsSupervisorStepState):
        raise TypeError("initial_state must be DemucsSupervisorStepState.")
    if not callable(session_factory):
        raise TypeError("session_factory must be callable.")

    current_state = initial_state
    completed_cycles = 0
    last_cycle: DemucsSupervisorOnceResult | None = None

    while True:
        if _shutdown_requested_before_cycle(shutdown_waiter):
            return DemucsSupervisorLoopResult(
                outcome=DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
                final_state=current_state,
                completed_cycles=completed_cycles,
                final_cycle=last_cycle,
            )

        cycle = _run_one_recovery_or_normal_cycle(
            state=current_state,
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=work_directory,
            shutdown_waiter=shutdown_waiter,
            session_factory=session_factory,
            ffprobe_runner=ffprobe_runner,
            demucs_runner=demucs_runner,
            uploader=uploader,
            event_id_factory=event_id_factory,
            pre_model_retry_after_seconds=pre_model_retry_after_seconds,
            running_retry_after_seconds=running_retry_after_seconds,
            jitter_fraction=jitter_fraction,
        )
        if not isinstance(cycle, DemucsSupervisorOnceResult):
            raise TypeError("Demucs supervisor runner returned an invalid result.")
        completed_cycles += 1
        current_state = cycle.next_state
        if cycle.action_result.outcome is DemucsSupervisorActionOutcome.CONTINUE:
            last_cycle = cycle
            continue
        if cycle.action_result.outcome is DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED:
            return DemucsSupervisorLoopResult(
                outcome=DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
                final_state=current_state,
                completed_cycles=completed_cycles,
                final_cycle=cycle,
            )
        if cycle.action_result.outcome is DemucsSupervisorActionOutcome.EXIT_FATAL:
            return DemucsSupervisorLoopResult(
                outcome=DemucsSupervisorLoopOutcome.EXIT_FATAL,
                final_state=current_state,
                completed_cycles=completed_cycles,
                final_cycle=cycle,
            )
        raise TypeError("Demucs supervisor action outcome is invalid.")
