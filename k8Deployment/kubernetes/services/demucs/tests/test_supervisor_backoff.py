"""Unit tests for Demucs's pure supervisor decision and backoff policy.

These tests exercise data objects and pure functions only: no sleep, RabbitMQ,
PostgreSQL, MinIO, FFprobe/Demucs process, image, or Kubernetes action occurs.
"""

from __future__ import annotations

from datetime import UTC, datetime
import unittest

from app.pre_model_failure_runtime import DemucsOneTaskExecution, DemucsOneTaskExecutionOutcome
from app.receive_execute_once import DemucsWorkerIterationOutcome, DemucsWorkerIterationResult
from app.recovery_execute_once import DemucsRecoveryIterationOutcome, DemucsRecoveryIterationResult
from app.supervisor_backoff import (
    DEFAULT_DEMUCS_IDLE_DELAY_SECONDS,
    DEFAULT_DEMUCS_RETRY_MAX_DELAY_SECONDS,
    DemucsSupervisorAction,
    DemucsSupervisorBackoffState,
    DemucsSupervisorDecision,
    DemucsSupervisorEvent,
    next_demucs_supervisor_decision,
    supervisor_event_for_demucs_iteration,
    supervisor_event_for_demucs_recovery_iteration,
)
from app.task_lease import DEMUCS_EXHAUSTED_LEASE_ERROR_CODE, DemucsExpiredLeaseTerminalization


class DemucsSupervisorIterationClassificationTests(unittest.TestCase):
    """Prove compact worker facts map to the correct future policy event."""

    def test_only_normal_broker_idle_waits_while_other_normal_results_progress(self) -> None:
        """Duplicate/DLQ outcomes must not create a needless poll delay."""

        execution = DemucsOneTaskExecution(DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        expected_events = {
            DemucsWorkerIterationOutcome.IDLE: DemucsSupervisorEvent.ITERATION_IDLE,
            DemucsWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK: DemucsSupervisorEvent.ITERATION_PROGRESS,
            DemucsWorkerIterationOutcome.MALFORMED_REJECTED: DemucsSupervisorEvent.ITERATION_PROGRESS,
            DemucsWorkerIterationOutcome.EXECUTED: DemucsSupervisorEvent.ITERATION_PROGRESS,
        }
        for outcome, expected_event in expected_events.items():
            with self.subTest(outcome=outcome):
                result = DemucsWorkerIterationResult(
                    outcome=outcome,
                    execution=execution if outcome is DemucsWorkerIterationOutcome.EXECUTED else None,
                )
                self.assertEqual(supervisor_event_for_demucs_iteration(result), expected_event)

    def test_iteration_classifier_rejects_non_result_input(self) -> None:
        """A caught exception cannot be relabeled as normal iteration work."""

        with self.assertRaises(TypeError):
            supervisor_event_for_demucs_iteration(object())  # type: ignore[arg-type]

    def test_every_valid_recovery_result_progresses_to_its_normal_broker_turn(self) -> None:
        """Recovery idle/finalization must not insert a delay before normal work."""

        exhausted = DemucsExpiredLeaseTerminalization(
            task_id="00000000-0000-4000-8000-000000000001",
            job_id="00000000-0000-4000-8000-000000000002",
            attempt_count=3,
            completed_at=datetime(2026, 9, 21, 12, 30, tzinfo=UTC),
            job_revision=8,
            error_code=DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
        )
        for outcome, maybe_execution, maybe_terminalization in (
            (DemucsRecoveryIterationOutcome.IDLE, None, None),
            (
                DemucsRecoveryIterationOutcome.EXECUTED,
                DemucsOneTaskExecution(DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST),
                None,
            ),
            (DemucsRecoveryIterationOutcome.TERMINALIZED, None, exhausted),
        ):
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    supervisor_event_for_demucs_recovery_iteration(
                        DemucsRecoveryIterationResult(
                            outcome,
                            execution=maybe_execution,
                            terminalization=maybe_terminalization,
                        ),
                    ),
                    DemucsSupervisorEvent.ITERATION_PROGRESS,
                )

        with self.assertRaises(TypeError):
            supervisor_event_for_demucs_recovery_iteration(object())  # type: ignore[arg-type]


class DemucsSupervisorBackoffPolicyTests(unittest.TestCase):
    """Prove bounded backoff, healthy reset, and visible fatal-exit behavior."""

    def test_retryable_failure_uses_capped_exponential_delays(self) -> None:
        """Zero-jitter delays are 1, 2, 4, 8, 16, then capped at 30 seconds."""

        state = DemucsSupervisorBackoffState()
        expected_delays = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0)
        expected_streaks = (1, 2, 3, 4, 5, 6, 6)
        for expected_delay, expected_streak in zip(expected_delays, expected_streaks, strict=True):
            decision = next_demucs_supervisor_decision(
                state,
                DemucsSupervisorEvent.RETRYABLE_FAILURE,
                jitter_fraction=0.0,
            )
            self.assertEqual(decision.action, DemucsSupervisorAction.RETRY_AFTER_BACKOFF)
            self.assertEqual(decision.delay_seconds, expected_delay)
            self.assertEqual(decision.next_state.retryable_failure_streak, expected_streak)
            state = decision.next_state

    def test_maximum_jitter_and_streak_remain_within_the_hard_delay_bound(self) -> None:
        """Runtime-provided jitter cannot manufacture an unbounded wait."""

        middle_decision = next_demucs_supervisor_decision(
            DemucsSupervisorBackoffState(retryable_failure_streak=2),
            DemucsSupervisorEvent.RETRYABLE_FAILURE,
            jitter_fraction=1.0,
        )
        self.assertEqual(middle_decision.delay_seconds, 5.0)

        capped_decision = next_demucs_supervisor_decision(
            DemucsSupervisorBackoffState(retryable_failure_streak=6),
            DemucsSupervisorEvent.RETRYABLE_FAILURE,
            jitter_fraction=1.0,
        )
        self.assertEqual(capped_decision.delay_seconds, DEFAULT_DEMUCS_RETRY_MAX_DELAY_SECONDS)

    def test_normal_idle_or_progress_resets_previous_failure_state(self) -> None:
        """A normal boundary restores short healthy polling behavior."""

        failed_state = DemucsSupervisorBackoffState(retryable_failure_streak=4)
        idle_decision = next_demucs_supervisor_decision(
            failed_state,
            DemucsSupervisorEvent.ITERATION_IDLE,
        )
        self.assertEqual(idle_decision.action, DemucsSupervisorAction.WAIT_IDLE)
        self.assertEqual(idle_decision.delay_seconds, DEFAULT_DEMUCS_IDLE_DELAY_SECONDS)
        self.assertEqual(idle_decision.next_state.retryable_failure_streak, 0)

        progress_decision = next_demucs_supervisor_decision(
            failed_state,
            DemucsSupervisorEvent.ITERATION_PROGRESS,
        )
        self.assertEqual(progress_decision.action, DemucsSupervisorAction.CHECK_IMMEDIATELY)
        self.assertEqual(progress_decision.delay_seconds, 0.0)
        self.assertEqual(progress_decision.next_state.retryable_failure_streak, 0)

    def test_fatal_configuration_exits_immediately_without_retry_state(self) -> None:
        """Bad static configuration cannot be silently retried forever."""

        decision = next_demucs_supervisor_decision(
            DemucsSupervisorBackoffState(retryable_failure_streak=3),
            DemucsSupervisorEvent.FATAL_CONFIGURATION,
        )
        self.assertEqual(decision.action, DemucsSupervisorAction.EXIT_FATAL)
        self.assertEqual(decision.delay_seconds, 0.0)
        self.assertEqual(decision.next_state.retryable_failure_streak, 0)

    def test_invalid_policy_inputs_are_rejected_before_a_delay_is_planned(self) -> None:
        """Invalid state/event/jitter cannot silently turn into runtime waiting."""

        with self.assertRaises(ValueError):
            DemucsSupervisorBackoffState(retryable_failure_streak=7)
        with self.assertRaises(TypeError):
            next_demucs_supervisor_decision(
                object(),  # type: ignore[arg-type]
                DemucsSupervisorEvent.ITERATION_IDLE,
            )
        with self.assertRaises(TypeError):
            next_demucs_supervisor_decision(
                DemucsSupervisorBackoffState(),
                "retryable_failure",  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            next_demucs_supervisor_decision(
                DemucsSupervisorBackoffState(),
                DemucsSupervisorEvent.RETRYABLE_FAILURE,
                jitter_fraction=1.1,
            )
        with self.assertRaises(ValueError):
            DemucsSupervisorDecision(
                action=DemucsSupervisorAction.CHECK_IMMEDIATELY,
                delay_seconds=DEFAULT_DEMUCS_IDLE_DELAY_SECONDS,
                next_state=DemucsSupervisorBackoffState(),
            )


if __name__ == "__main__":
    unittest.main()
