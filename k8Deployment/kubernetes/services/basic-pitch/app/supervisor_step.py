"""Compose one fair Basic Pitch iteration into one supervisor decision.

This is deliberately one *step*, not a long-running worker.  It invokes the
existing two-source fair iteration once, maps its normal `progress`/`idle`
result to the established backoff decision, and carries forward both local
fairness preference and local failure-backoff state.  If the iteration raises,
only the narrow supervisor failure classifier may turn that error into a
retryable or fatal decision; every unclassified error escapes unchanged.

The future runtime owns the actual ``while`` loop, interruptible sleep,
RabbitMQ connection/channel lifecycle, readiness reporting, process signals,
and Kubernetes termination behavior.  This module performs none of those
actions itself.  It merely makes one bounded call and returns immutable next
state, which keeps failure/backoff semantics unit-testable before the worker
holds long-lived clients or runs continuously.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.basic_pitch_process import BasicPitchProcessRunner, DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS
from app.basic_pitch_task_execution import BasicPitchTaskExecutionStorageClient
from app.supervisor_backoff import (
    BasicPitchSupervisorAction,
    BasicPitchSupervisorBackoffState,
    BasicPitchSupervisorDecision,
    BasicPitchSupervisorEvent,
    next_basic_pitch_supervisor_decision,
    supervisor_event_for_basic_pitch_fair_work_iteration,
)
from app.supervisor_failure_classification import classify_basic_pitch_supervisor_failure
from app.work_schedule import BasicPitchWorkScheduleState
from app.work_source_iteration import (
    BasicPitchFairWorkIterationDatabase,
    BasicPitchFairWorkIterationResult,
    run_one_fair_basic_pitch_work_iteration,
)


@dataclass(frozen=True)
class BasicPitchSupervisorStepState:
    """The two small local state values retained between supervisor steps.

    Neither field is durable task state. ``schedule_state`` makes source
    selection fair, and ``backoff_state`` bounds retries after worker-level
    outages. A Pod restart can discard both: RabbitMQ and PostgreSQL still
    retain all authoritative work and lifecycle records.
    """

    schedule_state: BasicPitchWorkScheduleState = BasicPitchWorkScheduleState()
    backoff_state: BasicPitchSupervisorBackoffState = BasicPitchSupervisorBackoffState()

    def __post_init__(self) -> None:
        """Reject forged local state before it can steer the next runtime call."""

        if not isinstance(self.schedule_state, BasicPitchWorkScheduleState):
            raise TypeError("Basic Pitch supervisor schedule state is invalid.")
        if not isinstance(self.backoff_state, BasicPitchSupervisorBackoffState):
            raise TypeError("Basic Pitch supervisor backoff state is invalid.")


@dataclass(frozen=True)
class BasicPitchSupervisorStepResult:
    """One normal iteration or classified failure paired with its next action.

    A normal iteration retains its compact fair result and advances fair
    scheduling state. A classified failure intentionally has no iteration
    result and preserves the previous schedule state: an incomplete source
    attempt must not pretend it made normal fair progress. In both cases the
    returned decision's backoff state becomes the next local backoff state.
    """

    event: BasicPitchSupervisorEvent
    decision: BasicPitchSupervisorDecision
    next_state: BasicPitchSupervisorStepState
    iteration: BasicPitchFairWorkIterationResult | None = None

    def __post_init__(self) -> None:
        """Make normal-result versus classified-failure pairings explicit."""

        if not isinstance(self.event, BasicPitchSupervisorEvent):
            raise TypeError("Basic Pitch supervisor step event is invalid.")
        if not isinstance(self.decision, BasicPitchSupervisorDecision):
            raise TypeError("Basic Pitch supervisor step decision is invalid.")
        if not isinstance(self.next_state, BasicPitchSupervisorStepState):
            raise TypeError("Basic Pitch supervisor step next state is invalid.")
        if self.event in (
            BasicPitchSupervisorEvent.ITERATION_IDLE,
            BasicPitchSupervisorEvent.ITERATION_PROGRESS,
        ):
            if not isinstance(self.iteration, BasicPitchFairWorkIterationResult):
                raise ValueError("A normal Basic Pitch supervisor event requires an iteration result.")
            if self.next_state.schedule_state != self.iteration.next_state:
                raise ValueError("A normal Basic Pitch supervisor step must advance fair schedule state.")
        elif self.event in (
            BasicPitchSupervisorEvent.RETRYABLE_FAILURE,
            BasicPitchSupervisorEvent.FATAL_CONFIGURATION,
        ):
            if self.iteration is not None:
                raise ValueError("A classified Basic Pitch supervisor failure cannot include iteration evidence.")
        else:  # Defensive for future enum changes.
            raise ValueError("Basic Pitch supervisor step event is invalid.")
        if self.next_state.backoff_state != self.decision.next_state:
            raise ValueError("Basic Pitch supervisor step must retain its decision backoff state.")
        if self.event is BasicPitchSupervisorEvent.RETRYABLE_FAILURE and (
            self.decision.action is not BasicPitchSupervisorAction.RETRY_AFTER_BACKOFF
        ):
            raise ValueError("A retryable Basic Pitch supervisor failure requires backoff.")
        if self.event is BasicPitchSupervisorEvent.FATAL_CONFIGURATION and (
            self.decision.action is not BasicPitchSupervisorAction.EXIT_FATAL
        ):
            raise ValueError("A fatal Basic Pitch supervisor failure requires exit.")


def run_one_basic_pitch_supervisor_step(
    channel: Any,
    *,
    state: BasicPitchSupervisorStepState,
    database: BasicPitchFairWorkIterationDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
    jitter_fraction: float = 0.0,
) -> BasicPitchSupervisorStepResult:
    """Run one fair iteration and return its next action/state without acting on it.

    On a normal result, fair scheduling moves to the returned iteration's next
    source and the backoff policy resets or chooses the short both-sources-idle
    delay. On a classified retryable/fatal exception, fairness state remains
    unchanged because the selected source did not complete a normal bounded
    attempt; only backoff state changes according to the reviewed decision.

    This function catches only ``Exception`` so process interrupts such as
    ``KeyboardInterrupt`` retain their normal shutdown semantics. It re-raises
    every unclassified exception exactly as received and does not sleep,
    reconnect, close a channel, or loop.
    """

    if not isinstance(state, BasicPitchSupervisorStepState):
        raise TypeError("state must be BasicPitchSupervisorStepState.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    try:
        iteration = run_one_fair_basic_pitch_work_iteration(
            channel,
            schedule_state=state.schedule_state,
            database=database,
            storage_client=storage_client,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
    except Exception as error:
        event = classify_basic_pitch_supervisor_failure(error)
        if event is None:
            raise
        decision = next_basic_pitch_supervisor_decision(
            state.backoff_state,
            event,
            jitter_fraction=jitter_fraction,
        )
        return BasicPitchSupervisorStepResult(
            event=event,
            decision=decision,
            next_state=BasicPitchSupervisorStepState(
                # Do not advance fair source preference after an incomplete
                # operation. The next post-backoff step can safely retry the
                # same reviewed source boundary.
                schedule_state=state.schedule_state,
                backoff_state=decision.next_state,
            ),
        )

    if not isinstance(iteration, BasicPitchFairWorkIterationResult):
        raise TypeError("Basic Pitch fair work iteration returned an invalid result.")
    event = supervisor_event_for_basic_pitch_fair_work_iteration(iteration)
    decision = next_basic_pitch_supervisor_decision(
        state.backoff_state,
        event,
        jitter_fraction=jitter_fraction,
    )
    return BasicPitchSupervisorStepResult(
        event=event,
        decision=decision,
        next_state=BasicPitchSupervisorStepState(
            schedule_state=iteration.next_state,
            backoff_state=decision.next_state,
        ),
        iteration=iteration,
    )
