"""Compose one cadence-selected ADTOF cycle into one supervisor decision.

This module makes exactly one bounded call to the normal-or-recovery worker
cycle. Normal AMQP results and delivery-free recovery results map to the
existing supervisor idle/progress events; a caught exception becomes a decision
only when the narrow failure classifier explicitly recognizes its type. All
other errors escape unchanged.

It is not a long-running worker: it has no loop, sleep, reconnect, connection
or channel lifecycle, signal handler, task mutation beyond the called
iteration, image entrypoint, or Kubernetes behavior. A later runtime must
apply the returned action with shutdown-aware waiting and explicit recovery
rules, rather than treating a decision as an already-performed operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.processing.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.runtime.claimed_task_success import ADTOFClaimedTaskSuccessStorageClient
from app.runtime.recovery_cadence import (
    ADTOFWorkerCadenceAction,
    ADTOFWorkerCadenceState,
    initial_adtof_worker_cadence_state,
)
from app.runtime.supervisor_backoff import (
    ADTOFSupervisorAction,
    ADTOFSupervisorBackoffState,
    ADTOFSupervisorDecision,
    ADTOFSupervisorEvent,
    next_adtof_supervisor_decision,
    supervisor_event_for_adtof_iteration,
    supervisor_event_for_adtof_recovery_iteration,
)
from app.runtime.supervisor_failure_classification import classify_adtof_supervisor_failure
from app.runtime.worker_cycle import ADTOFWorkerCycleDatabase, ADTOFWorkerCycleResult, run_one_adtof_worker_cycle


@dataclass(frozen=True)
class ADTOFSupervisorStepState:
    """The bounded local backoff and normal/recovery-cadence state.

    It is not a PostgreSQL task lease, RabbitMQ delivery state, or durable task
    retry count. Losing it on a Pod restart is safe: PostgreSQL and RabbitMQ
    retain authoritative task/delivery facts, while the new Pod starts its
    deterministic cadence with a recovery scan before normal AMQP work.
    """

    backoff_state: ADTOFSupervisorBackoffState = ADTOFSupervisorBackoffState()
    cadence_state: ADTOFWorkerCadenceState = field(
        default_factory=initial_adtof_worker_cadence_state,
    )

    def __post_init__(self) -> None:
        """Reject a forged state before it can influence a future wait/action."""

        if not isinstance(self.backoff_state, ADTOFSupervisorBackoffState):
            raise TypeError("ADTOF supervisor backoff state is invalid.")
        if not isinstance(self.cadence_state, ADTOFWorkerCadenceState):
            raise TypeError("ADTOF supervisor cadence state is invalid.")


@dataclass(frozen=True)
class ADTOFSupervisorStepResult:
    """One selected cycle or reviewed classified failure plus next state.

    Normal events retain their compact cycle result. A classified failure
    deliberately has no iteration evidence: no complete normal iteration
    occurred, and the future runtime must not incorrectly publish it as work
    progress. In all cases, the decision owns the next local backoff state.
    """

    event: ADTOFSupervisorEvent
    decision: ADTOFSupervisorDecision
    next_state: ADTOFSupervisorStepState
    cycle: ADTOFWorkerCycleResult | None = None

    def __post_init__(self) -> None:
        """Make normal-cycle and classified-failure pairings unambiguous."""

        if not isinstance(self.event, ADTOFSupervisorEvent):
            raise TypeError("ADTOF supervisor step event is invalid.")
        if not isinstance(self.decision, ADTOFSupervisorDecision):
            raise TypeError("ADTOF supervisor step decision is invalid.")
        if not isinstance(self.next_state, ADTOFSupervisorStepState):
            raise TypeError("ADTOF supervisor step next state is invalid.")

        if self.event in (
            ADTOFSupervisorEvent.ITERATION_IDLE,
            ADTOFSupervisorEvent.ITERATION_PROGRESS,
        ):
            if not isinstance(self.cycle, ADTOFWorkerCycleResult):
                raise ValueError("A normal ADTOF supervisor event requires a worker cycle result.")
            if self.next_state.cadence_state != self.cycle.next_state:
                raise ValueError("A normal ADTOF supervisor event must retain its advanced cadence state.")
        elif self.event in (
            ADTOFSupervisorEvent.RETRYABLE_FAILURE,
            ADTOFSupervisorEvent.FATAL_CONFIGURATION,
        ):
            if self.cycle is not None:
                raise ValueError("A classified ADTOF supervisor failure cannot include cycle evidence.")
        else:  # Defensive guard if the event vocabulary gains a new member.
            raise ValueError("ADTOF supervisor step event is invalid.")

        if self.next_state.backoff_state != self.decision.next_state:
            raise ValueError("ADTOF supervisor step must retain its decision backoff state.")
        if self.event is ADTOFSupervisorEvent.RETRYABLE_FAILURE and (
            self.decision.action is not ADTOFSupervisorAction.RETRY_AFTER_BACKOFF
        ):
            raise ValueError("A retryable ADTOF supervisor failure requires backoff.")
        if self.event is ADTOFSupervisorEvent.FATAL_CONFIGURATION and (
            self.decision.action is not ADTOFSupervisorAction.EXIT_FATAL
        ):
            raise ValueError("A fatal ADTOF supervisor failure requires exit.")


def run_one_adtof_supervisor_step(
    channel: Any,
    *,
    state: ADTOFSupervisorStepState,
    database: ADTOFWorkerCycleDatabase,
    storage_client: ADTOFClaimedTaskSuccessStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
    jitter_fraction: float = 0.0,
) -> ADTOFSupervisorStepResult:
    """Run one cadence-selected cycle and return next action/state without applying it.

    A normal-AMQP empty result receives the established short idle decision. A
    recovery idle is progress because it says nothing about the normal queue,
    which the advanced cadence schedules next. A recognized retryable/fatal
    exception changes only local backoff state and preserves the *prior*
    cadence action, so a later runtime retries the same branch after backoff.
    The function catches `Exception`, not `BaseException`, so
    `KeyboardInterrupt` and other process-control signals retain their normal
    termination semantics.
    """

    if not isinstance(state, ADTOFSupervisorStepState):
        raise TypeError("state must be ADTOFSupervisorStepState.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    try:
        cycle = run_one_adtof_worker_cycle(
            channel,
            state=state.cadence_state,
            database=database,
            storage_client=storage_client,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
    except Exception as error:
        event = classify_adtof_supervisor_failure(error)
        if event is None:
            raise
        decision = next_adtof_supervisor_decision(
            state.backoff_state,
            event,
            jitter_fraction=jitter_fraction,
        )
        return ADTOFSupervisorStepResult(
            event=event,
            decision=decision,
            # No cycle completed, so retain the selected action. This matters
            # for recovery fairness: an outage must not silently skip a
            # recovery scan and jump to normal AMQP work after backoff.
            next_state=ADTOFSupervisorStepState(
                backoff_state=decision.next_state,
                cadence_state=state.cadence_state,
            ),
        )

    if not isinstance(cycle, ADTOFWorkerCycleResult):
        raise TypeError("ADTOF worker cycle returned an invalid result.")
    if cycle.action is ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION:
        # `ADTOFWorkerCycleResult` validates the matching branch, but retain
        # this narrow type check before its fact crosses into supervisor policy.
        if cycle.normal_iteration is None:
            raise TypeError("ADTOF worker cycle normal iteration is invalid.")
        event = supervisor_event_for_adtof_iteration(cycle.normal_iteration)
    elif cycle.action is ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN:
        if cycle.recovery_iteration is None:
            raise TypeError("ADTOF worker cycle recovery iteration is invalid.")
        event = supervisor_event_for_adtof_recovery_iteration(cycle.recovery_iteration)
    else:  # A bypassed cycle object must not become a made-up normal result.
        raise TypeError("ADTOF worker cycle action is invalid.")
    decision = next_adtof_supervisor_decision(
        state.backoff_state,
        event,
        jitter_fraction=jitter_fraction,
    )
    return ADTOFSupervisorStepResult(
        event=event,
        decision=decision,
        next_state=ADTOFSupervisorStepState(
            backoff_state=decision.next_state,
            cadence_state=cycle.next_state,
        ),
        cycle=cycle,
    )
