"""Plan one safe delay after a bounded Basic Pitch worker iteration.

This module is deliberately a *policy*, not a worker runtime.  It has no
``while`` loop, ``sleep`` call, clock, random-number generator, AMQP
connection, PostgreSQL query, MinIO request, model invocation, signal handler,
image entrypoint, or Kubernetes API action.  A later runtime will:

1. run ``receive_and_execute_basic_pitch_once()`` exactly once;
2. classify its normal result or caught error into one event below;
3. ask this policy for the next action and delay; and then
4. perform the sleep/reconnect/exit action itself.

Keeping those jobs separate makes the retry timing deterministic and unit
testable.  It also keeps this module from accidentally deciding whether a
particular database, RabbitMQ, MinIO, or Basic Pitch process error is
retryable.  That error classification needs the real runtime's resource and
shutdown context and is intentionally a later, smaller task.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.receive_execute_once import BasicPitchWorkerIterationOutcome, BasicPitchWorkerIterationResult
from app.work_source_iteration import BasicPitchFairWorkIterationOutcome, BasicPitchFairWorkIterationResult


# A fair iteration checks both the normal RabbitMQ source and due PostgreSQL
# recovery source before it reports idle. This modest pause therefore happens
# only after both are empty, so it cannot delay a durable retry behind an empty
# broker poll. The future runtime, not this policy, makes it interruptible by
# SIGTERM.
DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS = 1.0

# A retryable connection/dependency failure waits 1, 2, 4, 8, 16, then at most
# 30 seconds.  Capping the streak at six has the same practical effect as
# tracking an unbounded failure count while preventing an ever-growing number
# from becoming state that callers must persist or expose in logs.
DEFAULT_BASIC_PITCH_RETRY_INITIAL_DELAY_SECONDS = 1.0
DEFAULT_BASIC_PITCH_RETRY_MAX_DELAY_SECONDS = 30.0
MAX_BASIC_PITCH_RETRY_FAILURE_STREAK = 6

# A supplied value in [0, 1] adds between 0% and 25% of the exponential base
# delay.  The final clamp keeps the actual result bounded by the stated
# 30-second maximum, even at the top of the sequence.
MAX_BASIC_PITCH_RETRY_JITTER_FRACTION = 0.25


class BasicPitchSupervisorEvent(StrEnum):
    """The intentionally small facts a future runtime gives to this policy.

    ``ITERATION_PROGRESS`` means the one-iteration boundary made a normal
    non-idle decision: it either completed work, safely acknowledged durable
    duplicate/stale history, or safely rejected malformed input to the DLQ.
    It does *not* claim that every model result succeeded; it only means the
    iteration completed normally and no failure backoff is currently needed.

    ``RETRYABLE_FAILURE`` and ``FATAL_CONFIGURATION`` are classifications,
    rather than exception classes.  The future runtime must make that
    classification explicitly; this pure layer intentionally cannot inspect
    live exception objects or choose a resource-specific recovery strategy.
    """

    ITERATION_IDLE = "iteration_idle"
    ITERATION_PROGRESS = "iteration_progress"
    RETRYABLE_FAILURE = "retryable_failure"
    FATAL_CONFIGURATION = "fatal_configuration"


class BasicPitchSupervisorAction(StrEnum):
    """The four actions a later runtime may take after reading a decision."""

    # A durable normal decision already happened, so drain any backlog without
    # adding a pointless delay between messages.
    CHECK_IMMEDIATELY = "check_immediately"
    # The broker assigned no delivery; wait briefly before another poll.
    WAIT_IDLE = "wait_idle"
    # Close/recreate resources as required by the runtime, then retry after a
    # bounded, jittered delay.  This is not an in-memory task retry.
    RETRY_AFTER_BACKOFF = "retry_after_backoff"
    # Invalid static configuration must end the process.  Kubernetes can then
    # surface/restart the Pod after the configuration or image is corrected.
    EXIT_FATAL = "exit_fatal"


@dataclass(frozen=True)
class BasicPitchSupervisorBackoffState:
    """Only the bounded retry-failure streak retained between iterations.

    The count is local process state, not a task lease or durable job state.
    RabbitMQ redelivery and PostgreSQL's idempotent task records remain the
    authority for work recovery if a Pod exits while this counter is nonzero.
    """

    retryable_failure_streak: int = 0

    def __post_init__(self) -> None:
        """Reject impossible states before they influence a retry delay."""

        if isinstance(self.retryable_failure_streak, bool) or not isinstance(
            self.retryable_failure_streak,
            int,
        ):
            raise ValueError("Basic Pitch retryable failure streak must be an integer.")
        if not 0 <= self.retryable_failure_streak <= MAX_BASIC_PITCH_RETRY_FAILURE_STREAK:
            raise ValueError(
                "Basic Pitch retryable failure streak must be between 0 and "
                f"{MAX_BASIC_PITCH_RETRY_FAILURE_STREAK}."
            )


@dataclass(frozen=True)
class BasicPitchSupervisorDecision:
    """A pure action and bounded delay for one next-step decision.

    ``delay_seconds`` is a recommendation only.  The future runtime supplies
    the actual interruptible sleep and must re-check its shutdown signal before
    acting, so a terminating Kubernetes Pod is never held by this value alone.
    """

    action: BasicPitchSupervisorAction
    delay_seconds: float
    next_state: BasicPitchSupervisorBackoffState

    def __post_init__(self) -> None:
        """Keep action/delay pairings explicit instead of trusting callers."""

        if not isinstance(self.action, BasicPitchSupervisorAction):
            raise ValueError("Basic Pitch supervisor action is invalid.")
        if isinstance(self.delay_seconds, bool) or not isinstance(self.delay_seconds, (int, float)):
            raise ValueError("Basic Pitch supervisor delay must be numeric.")
        if not 0 <= self.delay_seconds <= DEFAULT_BASIC_PITCH_RETRY_MAX_DELAY_SECONDS:
            raise ValueError("Basic Pitch supervisor delay is outside its bounded range.")
        if not isinstance(self.next_state, BasicPitchSupervisorBackoffState):
            raise ValueError("Basic Pitch supervisor next state is invalid.")

        # The result object is also a small contract at the future runtime
        # boundary.  It prevents a caller from accidentally treating an idle
        # result as an immediate busy-loop, or a fatal configuration problem as
        # a hidden sleep-and-retry decision.
        if self.action in (
            BasicPitchSupervisorAction.CHECK_IMMEDIATELY,
            BasicPitchSupervisorAction.EXIT_FATAL,
        ) and self.delay_seconds != 0:
            raise ValueError("Basic Pitch immediate/fatal action must not carry a delay.")
        if self.action is BasicPitchSupervisorAction.WAIT_IDLE and (
            self.delay_seconds != DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS
        ):
            raise ValueError("Basic Pitch idle action must carry the fixed idle delay.")
        if self.action is BasicPitchSupervisorAction.RETRY_AFTER_BACKOFF and (
            self.delay_seconds <= 0 or self.next_state.retryable_failure_streak == 0
        ):
            raise ValueError("Basic Pitch retry action requires delay and retry-failure state.")


def supervisor_event_for_basic_pitch_iteration(
    result: BasicPitchWorkerIterationResult,
) -> BasicPitchSupervisorEvent:
    """Classify one normal iteration without catching or hiding exceptions.

    Exceptions do not become a result at the one-iteration boundary, so a
    later runtime must classify them independently.  Among normal results,
    only ``IDLE`` needs a delay.  Acknowledged duplicate/stale history,
    malformed-DLQ rejection, and an execution result can immediately let the
    runtime check for the next delivery.
    """

    if not isinstance(result, BasicPitchWorkerIterationResult):
        raise TypeError("Basic Pitch supervisor requires a worker iteration result.")
    if result.outcome is BasicPitchWorkerIterationOutcome.IDLE:
        return BasicPitchSupervisorEvent.ITERATION_IDLE
    return BasicPitchSupervisorEvent.ITERATION_PROGRESS


def supervisor_event_for_basic_pitch_fair_work_iteration(
    result: BasicPitchFairWorkIterationResult,
) -> BasicPitchSupervisorEvent:
    """Classify one two-source normal result for the existing backoff policy.

    ``work_source_iteration.py`` performs up to two fair source checks before
    producing this result. Its `IDLE` outcome proves that *both* normal
    RabbitMQ delivery and due PostgreSQL retry recovery found nothing, making
    it the sole safe input for the short idle delay. Every `PROGRESS` result
    causes an immediate next fair selection and resets any local retry-failure
    streak. Exceptions do not become results here; the later runtime must
    classify them independently without hiding their original type.

    This adapter does not alter scheduling state, sleep, poll either source,
    reconnect, or change RabbitMQ/PostgreSQL/MinIO/model/Kubernetes state.
    """

    if not isinstance(result, BasicPitchFairWorkIterationResult):
        raise TypeError("Basic Pitch supervisor requires a fair work iteration result.")
    if result.outcome is BasicPitchFairWorkIterationOutcome.IDLE:
        return BasicPitchSupervisorEvent.ITERATION_IDLE
    return BasicPitchSupervisorEvent.ITERATION_PROGRESS


def _retry_delay_seconds(*, failure_streak: int, jitter_fraction: float) -> float:
    """Return one bounded exponential retry delay from injected jitter input.

    There is intentionally no call to ``random`` here.  A future runtime can
    choose a suitable source of entropy once per retry, while unit tests can
    provide an exact fraction.  The policy accepts a number from 0 through 1,
    which turns the exponential base into a delay up to 25% longer before the
    hard maximum is applied.
    """

    if isinstance(jitter_fraction, bool) or not isinstance(jitter_fraction, (int, float)):
        raise ValueError("Basic Pitch retry jitter fraction must be numeric.")
    if not 0 <= jitter_fraction <= 1:
        raise ValueError("Basic Pitch retry jitter fraction must be between 0 and 1.")

    # Streak one starts at the initial delay.  The state is already bounded at
    # six, so this exponent cannot grow without limit even before the final
    # maximum-delay clamp is applied.
    exponential_base = DEFAULT_BASIC_PITCH_RETRY_INITIAL_DELAY_SECONDS * (2 ** (failure_streak - 1))
    jittered_delay = exponential_base * (1 + (MAX_BASIC_PITCH_RETRY_JITTER_FRACTION * jitter_fraction))
    return min(jittered_delay, DEFAULT_BASIC_PITCH_RETRY_MAX_DELAY_SECONDS)


def next_basic_pitch_supervisor_decision(
    state: BasicPitchSupervisorBackoffState,
    event: BasicPitchSupervisorEvent,
    *,
    jitter_fraction: float = 0.0,
) -> BasicPitchSupervisorDecision:
    """Choose the bounded next action without sleeping or retrying anything.

    Normal idle and progress events reset a previous failure streak: receiving
    either proves this process reached a normal one-iteration boundary again.
    A retryable failure increments the local streak, calculates its capped
    exponential delay, and leaves task recovery to RabbitMQ/PostgreSQL.  A
    fatal configuration event returns ``EXIT_FATAL`` with no delay so a future
    entrypoint can end visibly rather than hiding a deterministic problem in an
    infinite reconnect loop.
    """

    if not isinstance(state, BasicPitchSupervisorBackoffState):
        raise TypeError("Basic Pitch supervisor requires a backoff state.")
    if not isinstance(event, BasicPitchSupervisorEvent):
        raise TypeError("Basic Pitch supervisor event is invalid.")

    reset_state = BasicPitchSupervisorBackoffState()
    if event is BasicPitchSupervisorEvent.ITERATION_PROGRESS:
        return BasicPitchSupervisorDecision(
            action=BasicPitchSupervisorAction.CHECK_IMMEDIATELY,
            delay_seconds=0.0,
            next_state=reset_state,
        )

    if event is BasicPitchSupervisorEvent.ITERATION_IDLE:
        return BasicPitchSupervisorDecision(
            action=BasicPitchSupervisorAction.WAIT_IDLE,
            delay_seconds=DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS,
            next_state=reset_state,
        )

    if event is BasicPitchSupervisorEvent.FATAL_CONFIGURATION:
        return BasicPitchSupervisorDecision(
            action=BasicPitchSupervisorAction.EXIT_FATAL,
            delay_seconds=0.0,
            next_state=reset_state,
        )

    # The only remaining enum member is RETRYABLE_FAILURE.  Saturating the
    # local counter at the last useful exponential step keeps the retry policy
    # bounded even when a dependency is unavailable for a long time.
    next_failure_streak = min(
        state.retryable_failure_streak + 1,
        MAX_BASIC_PITCH_RETRY_FAILURE_STREAK,
    )
    next_state = BasicPitchSupervisorBackoffState(next_failure_streak)
    return BasicPitchSupervisorDecision(
        action=BasicPitchSupervisorAction.RETRY_AFTER_BACKOFF,
        delay_seconds=_retry_delay_seconds(
            failure_streak=next_failure_streak,
            jitter_fraction=jitter_fraction,
        ),
        next_state=next_state,
    )
