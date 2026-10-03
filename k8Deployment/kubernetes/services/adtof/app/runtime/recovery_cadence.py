"""Choose a fair, bounded cadence between normal ADTOF work and lease recovery.

Normal RabbitMQ traffic can remain non-empty indefinitely. If recovery runs
only when ``basic_get`` is idle, a task stranded by a Pod crash may never be
reclaimed. This pure policy therefore starts every new worker cadence with one
PostgreSQL recovery scan and then strictly alternates:

``recovery scan -> one normal AMQP iteration -> recovery scan -> ...``

One normal task may include CPU inference, so this is intentionally a much
stronger bound than a large message-count interval: an expired task waits at
most one normal iteration before another Pod can try to reclaim it. An empty
recovery scan is a short no-mutation PostgreSQL check; it does not reserve a
RabbitMQ message or start a model. The separate worker-cycle composition
performs the chosen action; this module only returns immutable next-action
state and makes no database, MinIO, RabbitMQ, sleep, loop, image, Deployment,
or Kubernetes call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.runtime.recovery_execute_once import ADTOFRecoveryIterationOutcome, ADTOFRecoveryIterationResult
from app.runtime.receive_execute_once import ADTOFWorkerIterationOutcome, ADTOFWorkerIterationResult
from app.runtime.claimed_task_success import ADTOFClaimedTaskSuccess
from app.db.task_claim import ADTOFExpiredLeaseTerminalization


class ADTOFWorkerCadenceAction(StrEnum):
    """The single bounded action a future worker cycle may perform next."""

    # A new Pod scans recovery first so already-expired work is not delayed by
    # an arbitrary normal-message backlog.
    RUN_RECOVERY_SCAN = "run_recovery_scan"
    # One normal manual-ack receive/optional-execution iteration follows every
    # recovery attempt, whether it was idle, terminalized a third attempt, or
    # executed reclaimable work.
    RUN_NORMAL_ITERATION = "run_normal_iteration"


@dataclass(frozen=True)
class ADTOFWorkerCadenceState:
    """Minimal local scheduler state; it is not a durable task lease or retry."""

    next_action: ADTOFWorkerCadenceAction

    def __post_init__(self) -> None:
        """Reject a forged state before a future loop acts on it."""

        if not isinstance(self.next_action, ADTOFWorkerCadenceAction):
            raise TypeError("ADTOF worker cadence action is invalid.")


def initial_adtof_worker_cadence_state() -> ADTOFWorkerCadenceState:
    """Start with recovery to handle expired work present before this Pod started."""

    return ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN)


def _state_for(value: object, expected: ADTOFWorkerCadenceAction) -> ADTOFWorkerCadenceState:
    """Require the caller to report the result of the action this state chose."""

    if not isinstance(value, ADTOFWorkerCadenceState) or value.next_action is not expected:
        raise ValueError("ADTOF worker cadence transition is out of order.")
    return value


def _validate_normal_result(value: object) -> ADTOFWorkerIterationResult:
    """Accept one complete normal-iteration fact, never a forged partial result."""

    if not isinstance(value, ADTOFWorkerIterationResult):
        raise TypeError("ADTOF normal iteration result is invalid.")
    outcome = value.outcome
    execution = value.execution
    if not isinstance(outcome, ADTOFWorkerIterationOutcome):
        raise TypeError("ADTOF normal iteration result is invalid.")
    if outcome is ADTOFWorkerIterationOutcome.EXECUTED:
        if not isinstance(execution, ADTOFClaimedTaskSuccess):
            raise TypeError("ADTOF normal iteration result is invalid.")
    elif execution is not None:
        raise TypeError("ADTOF normal iteration result is invalid.")
    return value


def _validate_recovery_result(value: object) -> ADTOFRecoveryIterationResult:
    """Accept one complete recovery-iteration fact, never a forged partial result."""

    if not isinstance(value, ADTOFRecoveryIterationResult):
        raise TypeError("ADTOF recovery iteration result is invalid.")
    outcome = value.outcome
    execution = value.execution
    # ``getattr`` keeps the error category deterministic for a deliberately
    # forged frozen dataclass used by a boundary test; real constructed results
    # always have this field.
    terminalization = getattr(value, "terminalization", None)
    if not isinstance(outcome, ADTOFRecoveryIterationOutcome):
        raise TypeError("ADTOF recovery iteration result is invalid.")
    if outcome is ADTOFRecoveryIterationOutcome.EXECUTED:
        if not isinstance(execution, ADTOFClaimedTaskSuccess) or terminalization is not None:
            raise TypeError("ADTOF recovery iteration result is invalid.")
    elif outcome is ADTOFRecoveryIterationOutcome.TERMINALIZED:
        # A terminalized third attempt is durable progress but intentionally
        # has no model completion fact: no MinIO or CPU work may follow it.
        if not isinstance(terminalization, ADTOFExpiredLeaseTerminalization) or execution is not None:
            raise TypeError("ADTOF recovery iteration result is invalid.")
    elif execution is not None or terminalization is not None:
        raise TypeError("ADTOF recovery iteration result is invalid.")
    return value


def advance_after_adtof_normal_iteration(
    state: ADTOFWorkerCadenceState,
    result: ADTOFWorkerIterationResult,
) -> ADTOFWorkerCadenceState:
    """Schedule recovery immediately after any one completed normal iteration.

    This intentionally treats idle, acknowledged-no-work, malformed-DLQ, and
    executed normal results alike. No normal outcome may suppress a recovery
    scan, which prevents a continuously busy or malformed queue from starving
    expired active leases.
    """

    _state_for(state, ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION)
    _validate_normal_result(result)
    return ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN)


def advance_after_adtof_recovery_iteration(
    state: ADTOFWorkerCadenceState,
    result: ADTOFRecoveryIterationResult,
) -> ADTOFWorkerCadenceState:
    """Schedule one normal AMQP iteration after every completed recovery scan."""

    _state_for(state, ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN)
    _validate_recovery_result(result)
    return ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION)
