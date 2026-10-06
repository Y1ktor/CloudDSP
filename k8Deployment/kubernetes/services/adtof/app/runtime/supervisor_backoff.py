"""Plan the next ADTOF worker action without running a worker loop.

This is deliberately a deterministic policy, not an operational supervisor.
It does not poll RabbitMQ, sleep, open a PostgreSQL/MinIO connection, invoke
ADTOF, install signal handlers, build an image, or use Kubernetes. A later
runtime will separately:

1. run one cadence-selected normal-or-recovery worker cycle;
2. map its compact normal result, or a specifically classified caught failure,
   to an event below;
3. obtain a pure decision from this module; and
4. perform an interruptible wait, reconnect, or visible process exit.

The separation is intentional. In particular, this policy must not inspect an
exception and guess whether RabbitMQ, PostgreSQL, MinIO, or model failures are
safe to retry. That resource-specific classification needs a later focused
boundary and should remain visible to the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.runtime.recovery_execute_once import ADTOFRecoveryIterationResult
from app.runtime.receive_execute_once import ADTOFWorkerIterationOutcome, ADTOFWorkerIterationResult


# Only an empty normal broker poll receives the short delay. Acknowledged
# duplicate/stale or malformed work is still progress because another queued
# delivery may be ready immediately behind it.
DEFAULT_ADTOF_IDLE_DELAY_SECONDS = 1.0

# A future failure classifier may select the retryable event. This local
# process-only sequence is bounded: 1, 2, 4, 8, 16, then 30 seconds. Durable
# task retry belongs in PostgreSQL/RabbitMQ and is deliberately separate.
DEFAULT_ADTOF_RETRY_INITIAL_DELAY_SECONDS = 1.0
DEFAULT_ADTOF_RETRY_MAX_DELAY_SECONDS = 30.0
MAX_ADTOF_RETRY_FAILURE_STREAK = 6

# The future runtime supplies a random fraction rather than this pure policy
# obtaining entropy itself. The bounded 0%-25% addition reduces herd behavior
# after a shared dependency outage while keeping tests fully deterministic.
MAX_ADTOF_RETRY_JITTER_FRACTION = 0.25


class ADTOFSupervisorEvent(StrEnum):
    """The small facts a future runtime may provide to the decision policy.

    `ITERATION_PROGRESS` means one complete normal or recovery cycle either
    executed a current lease, handled a non-idle normal result, or completed a
    recovery scan. It does not imply that ADTOF necessarily produced MIDI. The
    two failure events are reserved for a future reviewed error-classification
    boundary, not inferred here.
    """

    ITERATION_IDLE = "iteration_idle"
    ITERATION_PROGRESS = "iteration_progress"
    RETRYABLE_FAILURE = "retryable_failure"
    FATAL_CONFIGURATION = "fatal_configuration"


class ADTOFSupervisorAction(StrEnum):
    """The next actions a later supervisor runtime can apply."""

    # Work was handled, so inspect the queue again without an avoidable gap.
    CHECK_IMMEDIATELY = "check_immediately"
    # The queue was empty; a future shutdown-aware waiter delays the next poll.
    WAIT_IDLE = "wait_idle"
    # The runtime will close/recreate affected resources after this delay.
    RETRY_AFTER_BACKOFF = "retry_after_backoff"
    # Bad static configuration must end visibly instead of retrying forever.
    EXIT_FATAL = "exit_fatal"


@dataclass(frozen=True)
class ADTOFSupervisorBackoffState:
    """The one bounded, local failure counter retained between iterations.

    This is neither task ownership nor durable retry state. If a Pod exits,
    RabbitMQ redelivery and PostgreSQL's task records recover the actual work;
    resetting this in-memory counter is therefore safe and expected.
    """

    retryable_failure_streak: int = 0

    def __post_init__(self) -> None:
        """Reject state that could create an unbounded or invalid delay."""

        if isinstance(self.retryable_failure_streak, bool) or not isinstance(
            self.retryable_failure_streak,
            int,
        ):
            raise ValueError("ADTOF retryable failure streak must be an integer.")
        if not 0 <= self.retryable_failure_streak <= MAX_ADTOF_RETRY_FAILURE_STREAK:
            raise ValueError(
                "ADTOF retryable failure streak must be between 0 and "
                f"{MAX_ADTOF_RETRY_FAILURE_STREAK}."
            )


@dataclass(frozen=True)
class ADTOFSupervisorDecision:
    """A pure next action, bounded delay, and state for the following step.

    `delay_seconds` is a recommendation, not an actual sleep. A later runtime
    must use a shutdown-aware waiter and re-check termination before acting, so
    this policy alone cannot delay Kubernetes Pod termination.
    """

    action: ADTOFSupervisorAction
    delay_seconds: float
    next_state: ADTOFSupervisorBackoffState

    def __post_init__(self) -> None:
        """Enforce valid action/delay/state combinations at the runtime seam."""

        if not isinstance(self.action, ADTOFSupervisorAction):
            raise ValueError("ADTOF supervisor action is invalid.")
        if isinstance(self.delay_seconds, bool) or not isinstance(self.delay_seconds, (int, float)):
            raise ValueError("ADTOF supervisor delay must be numeric.")
        if not 0 <= self.delay_seconds <= DEFAULT_ADTOF_RETRY_MAX_DELAY_SECONDS:
            raise ValueError("ADTOF supervisor delay is outside its bounded range.")
        if not isinstance(self.next_state, ADTOFSupervisorBackoffState):
            raise ValueError("ADTOF supervisor next state is invalid.")

        if self.action in (
            ADTOFSupervisorAction.CHECK_IMMEDIATELY,
            ADTOFSupervisorAction.EXIT_FATAL,
        ) and self.delay_seconds != 0:
            raise ValueError("ADTOF immediate/fatal action must not carry a delay.")
        if self.action is ADTOFSupervisorAction.WAIT_IDLE and (
            self.delay_seconds != DEFAULT_ADTOF_IDLE_DELAY_SECONDS
        ):
            raise ValueError("ADTOF idle action must carry the fixed idle delay.")
        if self.action is ADTOFSupervisorAction.RETRY_AFTER_BACKOFF and (
            self.delay_seconds <= 0 or self.next_state.retryable_failure_streak == 0
        ):
            raise ValueError("ADTOF retry action requires delay and retry-failure state.")


def supervisor_event_for_adtof_iteration(
    result: ADTOFWorkerIterationResult,
) -> ADTOFSupervisorEvent:
    """Classify one normal iteration without catching or hiding exceptions.

    Only `IDLE` needs a delay. A completed execution, durable no-work
    acknowledgement, or malformed-DLQ rejection is normal progress and should
    let a future runtime inspect the next message immediately. Exceptions are
    not normal results, so their later error classification stays explicit.
    """

    if not isinstance(result, ADTOFWorkerIterationResult):
        raise TypeError("ADTOF supervisor requires a worker iteration result.")
    if result.outcome is ADTOFWorkerIterationOutcome.IDLE:
        return ADTOFSupervisorEvent.ITERATION_IDLE
    return ADTOFSupervisorEvent.ITERATION_PROGRESS


def supervisor_event_for_adtof_recovery_iteration(
    result: ADTOFRecoveryIterationResult,
) -> ADTOFSupervisorEvent:
    """Map one recovery scan to immediate progress, including recovery idle.

    A recovery ``idle`` means only that no expired PostgreSQL task was found;
    a third-attempt ``terminalized`` result is likewise durable progress, not
    CPU work. Neither says anything about the normal RabbitMQ queue, which is
    scheduled next by the fairness cadence. Mapping every valid recovery fact
    to normal progress avoids adding the AMQP-empty one-second wait before that
    next broker poll. A recovery result is still structurally validated by its
    own dataclass before it reaches this policy; exceptions remain outside this
    normal-result mapper.
    """

    if not isinstance(result, ADTOFRecoveryIterationResult):
        raise TypeError("ADTOF supervisor requires a recovery iteration result.")
    return ADTOFSupervisorEvent.ITERATION_PROGRESS


def _retry_delay_seconds(*, failure_streak: int, jitter_fraction: float) -> float:
    """Calculate one capped exponential delay from caller-provided jitter."""

    if isinstance(jitter_fraction, bool) or not isinstance(jitter_fraction, (int, float)):
        raise ValueError("ADTOF retry jitter fraction must be numeric.")
    if not 0 <= jitter_fraction <= 1:
        raise ValueError("ADTOF retry jitter fraction must be between 0 and 1.")

    # State validation guarantees a finite exponent. Streak one starts at one
    # second and the final cap also bounds a maximum supplied jitter fraction.
    exponential_base = DEFAULT_ADTOF_RETRY_INITIAL_DELAY_SECONDS * (2 ** (failure_streak - 1))
    jittered_delay = exponential_base * (1 + (MAX_ADTOF_RETRY_JITTER_FRACTION * jitter_fraction))
    return min(jittered_delay, DEFAULT_ADTOF_RETRY_MAX_DELAY_SECONDS)


def next_adtof_supervisor_decision(
    state: ADTOFSupervisorBackoffState,
    event: ADTOFSupervisorEvent,
    *,
    jitter_fraction: float = 0.0,
) -> ADTOFSupervisorDecision:
    """Choose the next action without sleeping, reconnecting, or retrying work.

    Normal iteration events reset an earlier local failure streak. The reserved
    retryable event advances a capped in-memory count and returns a bounded
    delay. Fatal configuration returns an immediate visible exit action; it
    must never degrade into an infinite retry loop.
    """

    if not isinstance(state, ADTOFSupervisorBackoffState):
        raise TypeError("ADTOF supervisor requires a backoff state.")
    if not isinstance(event, ADTOFSupervisorEvent):
        raise TypeError("ADTOF supervisor event is invalid.")

    reset_state = ADTOFSupervisorBackoffState()
    if event is ADTOFSupervisorEvent.ITERATION_PROGRESS:
        return ADTOFSupervisorDecision(
            action=ADTOFSupervisorAction.CHECK_IMMEDIATELY,
            delay_seconds=0.0,
            next_state=reset_state,
        )
    if event is ADTOFSupervisorEvent.ITERATION_IDLE:
        return ADTOFSupervisorDecision(
            action=ADTOFSupervisorAction.WAIT_IDLE,
            delay_seconds=DEFAULT_ADTOF_IDLE_DELAY_SECONDS,
            next_state=reset_state,
        )
    if event is ADTOFSupervisorEvent.FATAL_CONFIGURATION:
        return ADTOFSupervisorDecision(
            action=ADTOFSupervisorAction.EXIT_FATAL,
            delay_seconds=0.0,
            next_state=reset_state,
        )

    # The only remaining enum member is `RETRYABLE_FAILURE`. Saturating the
    # counter prevents an outage from accumulating unbounded process state.
    next_failure_streak = min(
        state.retryable_failure_streak + 1,
        MAX_ADTOF_RETRY_FAILURE_STREAK,
    )
    next_state = ADTOFSupervisorBackoffState(next_failure_streak)
    return ADTOFSupervisorDecision(
        action=ADTOFSupervisorAction.RETRY_AFTER_BACKOFF,
        delay_seconds=_retry_delay_seconds(
            failure_streak=next_failure_streak,
            jitter_fraction=jitter_fraction,
        ),
        next_state=next_state,
    )
