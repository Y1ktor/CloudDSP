"""Unit tests for the pure Basic Pitch supervisor backoff policy.

These tests exercise only data objects and pure functions.  They do not sleep,
open RabbitMQ/PostgreSQL/MinIO connections, run Basic Pitch, start a worker
loop, create an image, or make Kubernetes state.
"""

from __future__ import annotations

import unittest

from app.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchClaimedTaskExecutionOutcome,
)
from app.receive_execute_once import BasicPitchWorkerIterationOutcome, BasicPitchWorkerIterationResult
from app.supervisor_backoff import (
    DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS,
    DEFAULT_BASIC_PITCH_RETRY_MAX_DELAY_SECONDS,
    BasicPitchSupervisorAction,
    BasicPitchSupervisorBackoffState,
    BasicPitchSupervisorDecision,
    BasicPitchSupervisorEvent,
    next_basic_pitch_supervisor_decision,
    supervisor_event_for_basic_pitch_iteration,
)


class BasicPitchSupervisorIterationClassificationTests(unittest.TestCase):
    """Prove normal receive-and-execute results map to only two safe events."""

    def test_idle_waits_but_all_other_normal_results_count_as_progress(self) -> None:
        """Only an empty queue should add the short polling delay."""

        execution = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
        )
        expected_events = {
            BasicPitchWorkerIterationOutcome.IDLE: BasicPitchSupervisorEvent.ITERATION_IDLE,
            BasicPitchWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK: BasicPitchSupervisorEvent.ITERATION_PROGRESS,
            BasicPitchWorkerIterationOutcome.MALFORMED_REJECTED: BasicPitchSupervisorEvent.ITERATION_PROGRESS,
            BasicPitchWorkerIterationOutcome.EXECUTED: BasicPitchSupervisorEvent.ITERATION_PROGRESS,
        }
        for outcome, expected_event in expected_events.items():
            with self.subTest(outcome=outcome):
                result = BasicPitchWorkerIterationResult(
                    outcome=outcome,
                    execution=execution if outcome is BasicPitchWorkerIterationOutcome.EXECUTED else None,
                )
                self.assertEqual(supervisor_event_for_basic_pitch_iteration(result), expected_event)

    def test_iteration_classifier_rejects_non_result_input(self) -> None:
        """A future runtime cannot substitute a caught exception for a normal result."""

        with self.assertRaises(TypeError):
            supervisor_event_for_basic_pitch_iteration(object())  # type: ignore[arg-type]


class BasicPitchSupervisorBackoffPolicyTests(unittest.TestCase):
    """Prove bounded retry timing, reset behavior, and explicit fatal exit."""

    def test_retryable_failure_uses_capped_exponential_delays(self) -> None:
        """The no-jitter sequence is 1, 2, 4, 8, 16, then a capped 30 seconds."""

        state = BasicPitchSupervisorBackoffState()
        expected_delays = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0)
        expected_streaks = (1, 2, 3, 4, 5, 6, 6)
        for expected_delay, expected_streak in zip(expected_delays, expected_streaks, strict=True):
            decision = next_basic_pitch_supervisor_decision(
                state,
                BasicPitchSupervisorEvent.RETRYABLE_FAILURE,
                jitter_fraction=0.0,
            )
            self.assertEqual(decision.action, BasicPitchSupervisorAction.RETRY_AFTER_BACKOFF)
            self.assertEqual(decision.delay_seconds, expected_delay)
            self.assertEqual(decision.next_state.retryable_failure_streak, expected_streak)
            state = decision.next_state

    def test_retry_jitter_is_bounded_even_at_the_maximum_delay(self) -> None:
        """A runtime-provided maximum jitter cannot exceed the 30-second policy bound."""

        middle_streak = BasicPitchSupervisorBackoffState(retryable_failure_streak=2)
        middle_decision = next_basic_pitch_supervisor_decision(
            middle_streak,
            BasicPitchSupervisorEvent.RETRYABLE_FAILURE,
            jitter_fraction=1.0,
        )
        self.assertEqual(middle_decision.delay_seconds, 5.0)

        max_streak = BasicPitchSupervisorBackoffState(retryable_failure_streak=6)
        max_decision = next_basic_pitch_supervisor_decision(
            max_streak,
            BasicPitchSupervisorEvent.RETRYABLE_FAILURE,
            jitter_fraction=1.0,
        )
        self.assertEqual(max_decision.delay_seconds, DEFAULT_BASIC_PITCH_RETRY_MAX_DELAY_SECONDS)

    def test_idle_and_progress_reset_a_previous_retryable_failure_streak(self) -> None:
        """A normal iteration proves this process reached a healthy boundary again."""

        failed_state = BasicPitchSupervisorBackoffState(retryable_failure_streak=4)
        idle_decision = next_basic_pitch_supervisor_decision(
            failed_state,
            BasicPitchSupervisorEvent.ITERATION_IDLE,
        )
        self.assertEqual(idle_decision.action, BasicPitchSupervisorAction.WAIT_IDLE)
        self.assertEqual(idle_decision.delay_seconds, DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS)
        self.assertEqual(idle_decision.next_state.retryable_failure_streak, 0)

        progress_decision = next_basic_pitch_supervisor_decision(
            failed_state,
            BasicPitchSupervisorEvent.ITERATION_PROGRESS,
        )
        self.assertEqual(progress_decision.action, BasicPitchSupervisorAction.CHECK_IMMEDIATELY)
        self.assertEqual(progress_decision.delay_seconds, 0.0)
        self.assertEqual(progress_decision.next_state.retryable_failure_streak, 0)

    def test_fatal_configuration_exits_without_backoff_or_retained_retry_state(self) -> None:
        """Bad static configuration must be visible instead of retried forever."""

        decision = next_basic_pitch_supervisor_decision(
            BasicPitchSupervisorBackoffState(retryable_failure_streak=3),
            BasicPitchSupervisorEvent.FATAL_CONFIGURATION,
        )
        self.assertEqual(decision.action, BasicPitchSupervisorAction.EXIT_FATAL)
        self.assertEqual(decision.delay_seconds, 0.0)
        self.assertEqual(decision.next_state.retryable_failure_streak, 0)

    def test_invalid_policy_inputs_are_rejected_before_a_delay_is_planned(self) -> None:
        """Bad control data cannot silently turn into an unsafe runtime wait."""

        with self.assertRaises(ValueError):
            BasicPitchSupervisorBackoffState(retryable_failure_streak=7)
        with self.assertRaises(TypeError):
            next_basic_pitch_supervisor_decision(
                object(),  # type: ignore[arg-type]
                BasicPitchSupervisorEvent.ITERATION_IDLE,
            )
        with self.assertRaises(TypeError):
            next_basic_pitch_supervisor_decision(
                BasicPitchSupervisorBackoffState(),
                "retryable_failure",  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            next_basic_pitch_supervisor_decision(
                BasicPitchSupervisorBackoffState(),
                BasicPitchSupervisorEvent.RETRYABLE_FAILURE,
                jitter_fraction=1.1,
            )
        with self.assertRaises(ValueError):
            BasicPitchSupervisorDecision(
                action=BasicPitchSupervisorAction.CHECK_IMMEDIATELY,
                delay_seconds=DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS,
                next_state=BasicPitchSupervisorBackoffState(),
            )


if __name__ == "__main__":
    unittest.main()
