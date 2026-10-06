"""Choose a fair, bounded cadence between normal Demucs work and recovery.

Normal RabbitMQ traffic may remain non-empty indefinitely.  Running recovery
only when ``basic_get`` is idle would let a task stranded by a Pod crash wait
behind that traffic forever.  This pure policy starts a new worker cadence
with one PostgreSQL recovery scan, then strictly alternates:

``recovery scan -> one normal AMQP iteration -> recovery scan -> ...``

One normal task can include CPU or future GPU inference, so this is a stronger
bound than a message-count interval: another recovery scan happens after at
most one normal iteration.  An idle recovery scan is a short no-mutation
PostgreSQL check; it does not reserve a RabbitMQ message or start Demucs.

The later worker-cycle composition performs the selected action.  This module
only returns immutable local scheduling state.  It makes no database, MinIO,
RabbitMQ, sleep, loop, image, Deployment, KEDA, or Kubernetes API call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.runtime.pre_model_failure_runtime import DemucsOneTaskExecution
from app.runtime.receive_execute_once import DemucsWorkerIterationOutcome, DemucsWorkerIterationResult
from app.runtime.recovery_execute_once import DemucsRecoveryIterationOutcome, DemucsRecoveryIterationResult
from app.db.task_lease import DemucsExpiredLeaseTerminalization


class DemucsWorkerCadenceAction(StrEnum):
    """The one bounded action a future worker cycle may take next."""

    # A restarted Pod scans durable recovery before queue traffic can delay an
    # already-expired active lease.
    RUN_RECOVERY_SCAN = "run_recovery_scan"
    # Every completed recovery decision yields exactly one normal manual-ack
    # receive/optional-execution iteration.
    RUN_NORMAL_ITERATION = "run_normal_iteration"


@dataclass(frozen=True)
class DemucsWorkerCadenceState:
    """Minimal local scheduler state, not a durable lease or task retry.

    Losing this state when a Pod restarts is safe. PostgreSQL retains task
    ownership and RabbitMQ retains delivery state; a new Pod deterministically
    starts with recovery before receiving ordinary broker work.
    """

    next_action: DemucsWorkerCadenceAction

    def __post_init__(self) -> None:
        """Reject a forged action before future orchestration acts on it."""

        if not isinstance(self.next_action, DemucsWorkerCadenceAction):
            raise TypeError("Demucs worker cadence action is invalid.")


def initial_demucs_worker_cadence_state() -> DemucsWorkerCadenceState:
    """Start with recovery so pre-existing expired work is never backlog-starved."""

    return DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN)


def _state_for(value: object, expected: DemucsWorkerCadenceAction) -> DemucsWorkerCadenceState:
    """Require the completed result to match the action this state selected."""

    if not isinstance(value, DemucsWorkerCadenceState) or value.next_action is not expected:
        raise ValueError("Demucs worker cadence transition is out of order.")
    return value


def _validate_normal_result(value: object) -> DemucsWorkerIterationResult:
    """Accept only a complete normal-iteration fact, not partial caller data."""

    if not isinstance(value, DemucsWorkerIterationResult):
        raise TypeError("Demucs normal iteration result is invalid.")
    outcome = value.outcome
    execution = value.execution
    if not isinstance(outcome, DemucsWorkerIterationOutcome):
        raise TypeError("Demucs normal iteration result is invalid.")
    if outcome is DemucsWorkerIterationOutcome.EXECUTED:
        if not isinstance(execution, DemucsOneTaskExecution):
            raise TypeError("Demucs normal iteration result is invalid.")
    elif execution is not None:
        raise TypeError("Demucs normal iteration result is invalid.")
    return value


def _validate_recovery_result(value: object) -> DemucsRecoveryIterationResult:
    """Accept only a complete durable recovery fact, never a forged partial one."""

    if not isinstance(value, DemucsRecoveryIterationResult):
        raise TypeError("Demucs recovery iteration result is invalid.")
    outcome = value.outcome
    execution = value.execution
    terminalization = value.terminalization
    if not isinstance(outcome, DemucsRecoveryIterationOutcome):
        raise TypeError("Demucs recovery iteration result is invalid.")
    if outcome is DemucsRecoveryIterationOutcome.EXECUTED:
        if not isinstance(execution, DemucsOneTaskExecution) or terminalization is not None:
            raise TypeError("Demucs recovery iteration result is invalid.")
    elif outcome is DemucsRecoveryIterationOutcome.TERMINALIZED:
        # Final-attempt expiry is durable progress but intentionally does not
        # represent MinIO or model work, so it must contain terminal proof only.
        if not isinstance(terminalization, DemucsExpiredLeaseTerminalization) or execution is not None:
            raise TypeError("Demucs recovery iteration result is invalid.")
    elif execution is not None or terminalization is not None:
        raise TypeError("Demucs recovery iteration result is invalid.")
    return value


def advance_after_demucs_normal_iteration(
    state: DemucsWorkerCadenceState,
    result: DemucsWorkerIterationResult,
) -> DemucsWorkerCadenceState:
    """Schedule recovery immediately after any one completed normal iteration.

    Idle, duplicate/stale acknowledgement, malformed-message rejection, and
    execution all advance identically. No normal queue outcome can suppress
    recovery; that prevents permanently busy or malformed traffic from
    starving expired active leases.
    """

    _state_for(state, DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION)
    _validate_normal_result(result)
    return DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN)


def advance_after_demucs_recovery_iteration(
    state: DemucsWorkerCadenceState,
    result: DemucsRecoveryIterationResult,
) -> DemucsWorkerCadenceState:
    """Schedule exactly one normal AMQP iteration after each recovery scan."""

    _state_for(state, DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN)
    _validate_recovery_result(result)
    return DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION)
