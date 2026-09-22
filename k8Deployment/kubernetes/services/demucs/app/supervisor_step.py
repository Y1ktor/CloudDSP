"""Compose one cadence-selected Demucs cycle into one supervisor decision.

This module makes exactly one bounded normal-or-recovery worker-cycle call.
Normal AMQP and delivery-free recovery facts map to existing idle/progress
events; a caught exception becomes a decision only when the narrow failure
classifier explicitly recognizes its type. Every other exception escapes
unchanged.

It is not a long-running worker. It has no loop, sleep, reconnect, connection
or channel lifecycle, signal handler, task mutation beyond its selected cycle,
image entrypoint, or Kubernetes behavior. A later runtime must apply the
returned action with shutdown-aware waiting and explicit resource recovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from app.demucs_artifact_upload import DemucsPutObjectClient
from app.demucs_process import DemucsProcessRunner
from app.ffprobe_process import DemucsFFprobeRunner
from app.planned_stem_upload import DemucsPlannedStemUploader
from app.pre_model_failure_transition import DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
from app.recovery_cadence import (
    DemucsWorkerCadenceAction,
    DemucsWorkerCadenceState,
    initial_demucs_worker_cadence_state,
)
from app.running_failure_transition import DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS
from app.source_preflight import DemucsSourcePreflightClient
from app.supervisor_backoff import (
    DemucsSupervisorAction,
    DemucsSupervisorBackoffState,
    DemucsSupervisorDecision,
    DemucsSupervisorEvent,
    next_demucs_supervisor_decision,
    supervisor_event_for_demucs_iteration,
    supervisor_event_for_demucs_recovery_iteration,
)
from app.supervisor_failure_classification import classify_demucs_supervisor_failure
from app.worker_cycle import DemucsWorkerCycleDatabase, DemucsWorkerCycleResult, run_one_demucs_worker_cycle


@dataclass(frozen=True)
class DemucsSupervisorStepState:
    """Bounded local backoff plus normal/recovery cadence state.

    This is not a PostgreSQL lease, RabbitMQ delivery record, or durable task
    retry count. Losing it on Pod restart is safe: PostgreSQL/RabbitMQ retain
    authoritative work facts, while a fresh Pod begins cadence with recovery
    before it receives ordinary AMQP work.
    """

    backoff_state: DemucsSupervisorBackoffState = DemucsSupervisorBackoffState()
    cadence_state: DemucsWorkerCadenceState = field(
        default_factory=initial_demucs_worker_cadence_state,
    )

    def __post_init__(self) -> None:
        """Reject forged local state before a later runtime uses it to act."""

        if not isinstance(self.backoff_state, DemucsSupervisorBackoffState):
            raise TypeError("Demucs supervisor backoff state is invalid.")
        if not isinstance(self.cadence_state, DemucsWorkerCadenceState):
            raise TypeError("Demucs supervisor cadence state is invalid.")


@dataclass(frozen=True)
class DemucsSupervisorStepResult:
    """One completed cycle or classified failure, decision, and next state.

    Normal events retain compact worker-cycle evidence. A classified failure
    intentionally retains no cycle: no bounded cycle completed, so callers
    cannot report it as work progress. In either case the decision owns the
    next local backoff state; task/broker ownership stays below this boundary.
    """

    event: DemucsSupervisorEvent
    decision: DemucsSupervisorDecision
    next_state: DemucsSupervisorStepState
    cycle: DemucsWorkerCycleResult | None = None

    def __post_init__(self) -> None:
        """Make normal-cycle and classified-failure evidence unambiguous."""

        if not isinstance(self.event, DemucsSupervisorEvent):
            raise TypeError("Demucs supervisor step event is invalid.")
        if not isinstance(self.decision, DemucsSupervisorDecision):
            raise TypeError("Demucs supervisor step decision is invalid.")
        if not isinstance(self.next_state, DemucsSupervisorStepState):
            raise TypeError("Demucs supervisor step next state is invalid.")

        if self.event in (
            DemucsSupervisorEvent.ITERATION_IDLE,
            DemucsSupervisorEvent.ITERATION_PROGRESS,
        ):
            if not isinstance(self.cycle, DemucsWorkerCycleResult):
                raise ValueError("A normal Demucs supervisor event requires a worker cycle result.")
            if self.next_state.cadence_state != self.cycle.next_state:
                raise ValueError("A normal Demucs supervisor event must retain advanced cadence state.")
        elif self.event in (
            DemucsSupervisorEvent.RETRYABLE_FAILURE,
            DemucsSupervisorEvent.FATAL_CONFIGURATION,
        ):
            if self.cycle is not None:
                raise ValueError("A classified Demucs supervisor failure cannot include cycle evidence.")
        else:  # Defensive guard for bypassed/future event vocabulary.
            raise ValueError("Demucs supervisor step event is invalid.")

        if self.next_state.backoff_state != self.decision.next_state:
            raise ValueError("Demucs supervisor step must retain its decision backoff state.")
        if self.event is DemucsSupervisorEvent.RETRYABLE_FAILURE and (
            self.decision.action is not DemucsSupervisorAction.RETRY_AFTER_BACKOFF
        ):
            raise ValueError("A retryable Demucs supervisor failure requires backoff.")
        if self.event is DemucsSupervisorEvent.FATAL_CONFIGURATION and (
            self.decision.action is not DemucsSupervisorAction.EXIT_FATAL
        ):
            raise ValueError("A fatal Demucs supervisor failure requires exit.")


def run_one_demucs_supervisor_step(
    channel: Any,
    *,
    state: DemucsSupervisorStepState,
    database: DemucsWorkerCycleDatabase,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
    pre_model_retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    running_retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
    jitter_fraction: float = 0.0,
) -> DemucsSupervisorStepResult:
    """Run one selected cycle and return, but never apply, its next action.

    Normal AMQP idle gets the short policy wait; recovery idle is progress
    because cadence schedules the normal broker receive next. A classified
    retryable/fatal exception changes local backoff only and preserves the
    prior cadence action, ensuring an outage cannot silently skip a recovery
    scan. ``Exception`` is caught—not ``BaseException``—so ``KeyboardInterrupt``
    and other process-control signals retain their normal termination behavior.
    """

    if not isinstance(state, DemucsSupervisorStepState):
        raise TypeError("state must be DemucsSupervisorStepState.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    try:
        cycle = run_one_demucs_worker_cycle(
            channel,
            state=state.cadence_state,
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=work_directory,
            ffprobe_runner=ffprobe_runner,
            demucs_runner=demucs_runner,
            uploader=uploader,
            event_id_factory=event_id_factory,
            pre_model_retry_after_seconds=pre_model_retry_after_seconds,
            running_retry_after_seconds=running_retry_after_seconds,
        )
    except Exception as error:
        event = classify_demucs_supervisor_failure(error)
        if event is None:
            raise
        decision = next_demucs_supervisor_decision(
            state.backoff_state,
            event,
            jitter_fraction=jitter_fraction,
        )
        return DemucsSupervisorStepResult(
            event=event,
            decision=decision,
            # No cycle completed, so retry the same selected action after any
            # future delay. This preserves fairness and recovery priority.
            next_state=DemucsSupervisorStepState(
                backoff_state=decision.next_state,
                cadence_state=state.cadence_state,
            ),
        )

    if not isinstance(cycle, DemucsWorkerCycleResult):
        raise TypeError("Demucs worker cycle returned an invalid result.")
    if cycle.action is DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION:
        if cycle.normal_iteration is None:
            raise TypeError("Demucs worker cycle normal iteration is invalid.")
        event = supervisor_event_for_demucs_iteration(cycle.normal_iteration)
    elif cycle.action is DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN:
        if cycle.recovery_iteration is None:
            raise TypeError("Demucs worker cycle recovery iteration is invalid.")
        event = supervisor_event_for_demucs_recovery_iteration(cycle.recovery_iteration)
    else:  # A bypassed cycle object must not manufacture a normal result.
        raise TypeError("Demucs worker cycle action is invalid.")

    decision = next_demucs_supervisor_decision(
        state.backoff_state,
        event,
        jitter_fraction=jitter_fraction,
    )
    return DemucsSupervisorStepResult(
        event=event,
        decision=decision,
        next_state=DemucsSupervisorStepState(
            backoff_state=decision.next_state,
            cadence_state=cycle.next_state,
        ),
        cycle=cycle,
    )
