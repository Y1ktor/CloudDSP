"""Plan a future Demucs worker action without running a worker loop.

This is deterministic local policy, not an operational supervisor. It does not
poll RabbitMQ, sleep, connect to PostgreSQL/MinIO, run FFprobe or Demucs,
install signal handling, build an image, or use Kubernetes. A later runtime
will separately:

1. run one cadence-selected worker cycle;
2. map its compact normal/recovery result, or a specifically classified caught
   failure, to an event below;
3. obtain this pure decision; and
4. perform an interruptible wait, reconnect, or visible process exit.

This policy intentionally cannot inspect an exception and guess whether a
RabbitMQ, PostgreSQL, MinIO, model, or process error is safe to retry. That
resource-specific classification needs a later narrow boundary and must remain
visible to the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.receive_execute_once import DemucsWorkerIterationOutcome, DemucsWorkerIterationResult
from app.recovery_execute_once import DemucsRecoveryIterationResult


# Only an empty *normal* broker poll receives the short delay. Acknowledged
# duplicate/stale and malformed work are progress because another delivery may
# already be ready; recovery idle similarly says nothing about the broker turn
# scheduled next by the alternating cadence.
DEFAULT_DEMUCS_IDLE_DELAY_SECONDS = 1.0

# A later reviewed failure classifier may choose RETRYABLE_FAILURE. This is a
# bounded process-local sequence: 1, 2, 4, 8, 16, then 30 seconds. Durable
# task retries remain PostgreSQL/RabbitMQ state and are not this counter.
DEFAULT_DEMUCS_RETRY_INITIAL_DELAY_SECONDS = 1.0
DEFAULT_DEMUCS_RETRY_MAX_DELAY_SECONDS = 30.0
MAX_DEMUCS_RETRY_FAILURE_STREAK = 6

# A future runtime supplies a random fraction, so this policy uses no entropy.
# It allows a 0%-25% addition to reduce herd reconnects after a shared outage
# while preserving deterministic unit tests and a hard maximum delay.
MAX_DEMUCS_RETRY_JITTER_FRACTION = 0.25


class DemucsSupervisorEvent(StrEnum):
    """Small facts a future runtime may provide to this decision policy.

    ``ITERATION_PROGRESS`` means one complete normal or recovery cycle made a
    non-idle normal decision or completed a recovery scan. It does not claim
    that a model produced stems. Failure events are reserved for a later
    reviewed error-classification boundary and are never inferred here.
    """

    ITERATION_IDLE = "iteration_idle"
    ITERATION_PROGRESS = "iteration_progress"
    RETRYABLE_FAILURE = "retryable_failure"
    FATAL_CONFIGURATION = "fatal_configuration"


class DemucsSupervisorAction(StrEnum):
    """Next actions a later shutdown-aware supervisor runtime may apply."""

    # Work/recovery completed normally, so inspect the next selected action
    # without an avoidable gap.
    CHECK_IMMEDIATELY = "check_immediately"
    # An ordinary broker receive was empty; an external, interruptible waiter
    # delays the next cycle without blocking SIGTERM itself.
    WAIT_IDLE = "wait_idle"
    # The runtime closes/recreates affected resources after this bounded delay.
    RETRY_AFTER_BACKOFF = "retry_after_backoff"
    # Static configuration cannot improve by waiting and must exit visibly.
    EXIT_FATAL = "exit_fatal"


@dataclass(frozen=True)
class DemucsSupervisorBackoffState:
    """One bounded local availability-failure counter between worker cycles.

    This is neither durable task ownership nor durable task retry state. A Pod
    exit safely loses it: RabbitMQ redelivery and PostgreSQL task records remain
    the source of truth for durable work recovery.
    """

    retryable_failure_streak: int = 0

    def __post_init__(self) -> None:
        """Reject state that could plan an invalid/unbounded retry delay."""

        if isinstance(self.retryable_failure_streak, bool) or not isinstance(
            self.retryable_failure_streak,
            int,
        ):
            raise ValueError("Demucs retryable failure streak must be an integer.")
        if not 0 <= self.retryable_failure_streak <= MAX_DEMUCS_RETRY_FAILURE_STREAK:
            raise ValueError(
                "Demucs retryable failure streak must be between 0 and "
                f"{MAX_DEMUCS_RETRY_FAILURE_STREAK}."
            )


@dataclass(frozen=True)
class DemucsSupervisorDecision:
    """Pure next action, bounded delay, and local state for the following step.

    ``delay_seconds`` is advice, not an actual sleep. A later runtime must wait
    in a shutdown-aware way and re-check termination before acting, so this
    object cannot independently delay Kubernetes Pod termination.
    """

    action: DemucsSupervisorAction
    delay_seconds: float
    next_state: DemucsSupervisorBackoffState

    def __post_init__(self) -> None:
        """Enforce action/delay/state pairings at the runtime boundary."""

        if not isinstance(self.action, DemucsSupervisorAction):
            raise ValueError("Demucs supervisor action is invalid.")
        if isinstance(self.delay_seconds, bool) or not isinstance(self.delay_seconds, (int, float)):
            raise ValueError("Demucs supervisor delay must be numeric.")
        if not 0 <= self.delay_seconds <= DEFAULT_DEMUCS_RETRY_MAX_DELAY_SECONDS:
            raise ValueError("Demucs supervisor delay is outside its bounded range.")
        if not isinstance(self.next_state, DemucsSupervisorBackoffState):
            raise ValueError("Demucs supervisor next state is invalid.")

        if self.action in (
            DemucsSupervisorAction.CHECK_IMMEDIATELY,
            DemucsSupervisorAction.EXIT_FATAL,
        ) and self.delay_seconds != 0:
            raise ValueError("Demucs immediate/fatal action must not carry a delay.")
        if self.action is DemucsSupervisorAction.WAIT_IDLE and (
            self.delay_seconds != DEFAULT_DEMUCS_IDLE_DELAY_SECONDS
        ):
            raise ValueError("Demucs idle action must carry the fixed idle delay.")
        if self.action is DemucsSupervisorAction.RETRY_AFTER_BACKOFF and (
            self.delay_seconds <= 0 or self.next_state.retryable_failure_streak == 0
        ):
            raise ValueError("Demucs retry action requires delay and retry-failure state.")


def supervisor_event_for_demucs_iteration(
    result: DemucsWorkerIterationResult,
) -> DemucsSupervisorEvent:
    """Map one normal iteration without catching/hiding any exception.

    Only an empty RabbitMQ receive receives a delay. A completed execution,
    durable duplicate/stale acknowledgement, and malformed-DLQ rejection are
    normal progress, so the runtime may immediately continue to its next
    cadence-selected action. Exceptions are not iteration results and need a
    later explicit classification.
    """

    if not isinstance(result, DemucsWorkerIterationResult):
        raise TypeError("Demucs supervisor requires a worker iteration result.")
    if result.outcome is DemucsWorkerIterationOutcome.IDLE:
        return DemucsSupervisorEvent.ITERATION_IDLE
    return DemucsSupervisorEvent.ITERATION_PROGRESS


def supervisor_event_for_demucs_recovery_iteration(
    result: DemucsRecoveryIterationResult,
) -> DemucsSupervisorEvent:
    """Map all recovery results to progress for their following normal turn.

    Recovery ``idle`` means only that PostgreSQL had no due/expired task; a
    terminalized third attempt is durable progress but no model work. Neither
    reveals normal RabbitMQ queue state, and cadence schedules an ordinary
    receive next. Treating every valid recovery result as progress avoids an
    unnecessary idle wait before that broker turn. Exceptions remain outside
    this mapper and are never relabeled as a harmless recovery scan.
    """

    if not isinstance(result, DemucsRecoveryIterationResult):
        raise TypeError("Demucs supervisor requires a recovery iteration result.")
    return DemucsSupervisorEvent.ITERATION_PROGRESS


def _retry_delay_seconds(*, failure_streak: int, jitter_fraction: float) -> float:
    """Calculate one capped exponential delay from caller-provided jitter."""

    if isinstance(jitter_fraction, bool) or not isinstance(jitter_fraction, (int, float)):
        raise ValueError("Demucs retry jitter fraction must be numeric.")
    if not 0 <= jitter_fraction <= 1:
        raise ValueError("Demucs retry jitter fraction must be between 0 and 1.")

    # State validation bounds the exponent. Streak one starts at one second,
    # and the final clamp also bounds maximum supplied jitter.
    exponential_base = DEFAULT_DEMUCS_RETRY_INITIAL_DELAY_SECONDS * (2 ** (failure_streak - 1))
    jittered_delay = exponential_base * (1 + (MAX_DEMUCS_RETRY_JITTER_FRACTION * jitter_fraction))
    return min(jittered_delay, DEFAULT_DEMUCS_RETRY_MAX_DELAY_SECONDS)


def next_demucs_supervisor_decision(
    state: DemucsSupervisorBackoffState,
    event: DemucsSupervisorEvent,
    *,
    jitter_fraction: float = 0.0,
) -> DemucsSupervisorDecision:
    """Choose a next action without waiting, reconnecting, or retrying work.

    Normal events reset an earlier local failure streak. A reserved retryable
    event advances a capped in-memory count and returns a bounded delay. A
    fatal configuration event returns a visible exit action rather than an
    infinite retry policy.
    """

    if not isinstance(state, DemucsSupervisorBackoffState):
        raise TypeError("Demucs supervisor requires a backoff state.")
    if not isinstance(event, DemucsSupervisorEvent):
        raise TypeError("Demucs supervisor event is invalid.")

    reset_state = DemucsSupervisorBackoffState()
    if event is DemucsSupervisorEvent.ITERATION_PROGRESS:
        return DemucsSupervisorDecision(
            action=DemucsSupervisorAction.CHECK_IMMEDIATELY,
            delay_seconds=0.0,
            next_state=reset_state,
        )
    if event is DemucsSupervisorEvent.ITERATION_IDLE:
        return DemucsSupervisorDecision(
            action=DemucsSupervisorAction.WAIT_IDLE,
            delay_seconds=DEFAULT_DEMUCS_IDLE_DELAY_SECONDS,
            next_state=reset_state,
        )
    if event is DemucsSupervisorEvent.FATAL_CONFIGURATION:
        return DemucsSupervisorDecision(
            action=DemucsSupervisorAction.EXIT_FATAL,
            delay_seconds=0.0,
            next_state=reset_state,
        )

    # The only remaining enum member is RETRYABLE_FAILURE. Saturation prevents
    # an outage from accumulating unbounded local process state.
    next_failure_streak = min(
        state.retryable_failure_streak + 1,
        MAX_DEMUCS_RETRY_FAILURE_STREAK,
    )
    next_state = DemucsSupervisorBackoffState(next_failure_streak)
    return DemucsSupervisorDecision(
        action=DemucsSupervisorAction.RETRY_AFTER_BACKOFF,
        delay_seconds=_retry_delay_seconds(
            failure_streak=next_failure_streak,
            jitter_fraction=jitter_fraction,
        ),
        next_state=next_state,
    )
