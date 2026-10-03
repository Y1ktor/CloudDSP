"""Unit tests for ADTOF's pure supervisor decision and backoff policy.

The tests exercise only data objects and pure functions: they never sleep,
poll RabbitMQ, connect to PostgreSQL/MinIO, run ADTOF, build an image, or
change Kubernetes state.
"""

from __future__ import annotations

from datetime import UTC, datetime
import unittest

from app.runtime.claimed_task_success import ADTOFClaimedTaskSuccess, ADTOFClaimedTaskSuccessOutcome
from app.runtime.recovery_execute_once import ADTOFRecoveryIterationOutcome, ADTOFRecoveryIterationResult
from app.runtime.receive_execute_once import ADTOFWorkerIterationOutcome, ADTOFWorkerIterationResult
from app.db.task_claim import ADTOFExpiredLeaseTerminalization
from app.runtime.supervisor_backoff import (
    DEFAULT_ADTOF_IDLE_DELAY_SECONDS,
    DEFAULT_ADTOF_RETRY_MAX_DELAY_SECONDS,
    ADTOFSupervisorAction,
    ADTOFSupervisorBackoffState,
    ADTOFSupervisorDecision,
    ADTOFSupervisorEvent,
    next_adtof_supervisor_decision,
    supervisor_event_for_adtof_iteration,
    supervisor_event_for_adtof_recovery_iteration,
)


class ADTOFSupervisorIterationClassificationTests(unittest.TestCase):
    """Prove normal worker outcomes map to the correct future action event."""

    def test_only_idle_waits_while_every_other_normal_result_is_progress(self) -> None:
        """Duplicate/DLQ results must not create a needless poll delay."""

        execution = ADTOFClaimedTaskSuccess(
            outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST,
        )
        expected_events = {
            ADTOFWorkerIterationOutcome.IDLE: ADTOFSupervisorEvent.ITERATION_IDLE,
            ADTOFWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK: ADTOFSupervisorEvent.ITERATION_PROGRESS,
            ADTOFWorkerIterationOutcome.MALFORMED_REJECTED: ADTOFSupervisorEvent.ITERATION_PROGRESS,
            ADTOFWorkerIterationOutcome.EXECUTED: ADTOFSupervisorEvent.ITERATION_PROGRESS,
        }
        for outcome, expected_event in expected_events.items():
            with self.subTest(outcome=outcome):
                result = ADTOFWorkerIterationResult(
                    outcome=outcome,
                    execution=execution if outcome is ADTOFWorkerIterationOutcome.EXECUTED else None,
                )
                self.assertEqual(supervisor_event_for_adtof_iteration(result), expected_event)

    def test_iteration_classifier_rejects_non_result_input(self) -> None:
        """A caught exception cannot be relabeled as a harmless normal iteration."""

        with self.assertRaises(TypeError):
            supervisor_event_for_adtof_iteration(object())  # type: ignore[arg-type]

    def test_every_valid_recovery_outcome_is_progress_for_the_next_normal_turn(self) -> None:
        """Recovery idle/terminalization must not suppress the normal AMQP poll."""

        exhausted = ADTOFExpiredLeaseTerminalization(
            task_id="9381d35a-355f-4fb1-bb39-32ceba7d917f",
            job_id="08ec1d44-3106-4fcb-91c8-5d0c78e7e046",
            stem_name="drums",
            attempt_count=3,
            completed_at=datetime(2026, 9, 14, 12, 30, tzinfo=UTC),
            error_code="lease_expired_attempts_exhausted",
        )
        for outcome, maybe_execution, maybe_terminalization in (
            (ADTOFRecoveryIterationOutcome.IDLE, None, None),
            (
                ADTOFRecoveryIterationOutcome.EXECUTED,
                ADTOFClaimedTaskSuccess(outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST),
                None,
            ),
            (ADTOFRecoveryIterationOutcome.TERMINALIZED, None, exhausted),
        ):
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    supervisor_event_for_adtof_recovery_iteration(
                        ADTOFRecoveryIterationResult(
                            outcome,
                            execution=maybe_execution,
                            terminalization=maybe_terminalization,
                        ),
                    ),
                    ADTOFSupervisorEvent.ITERATION_PROGRESS,
                )

        with self.assertRaises(TypeError):
            supervisor_event_for_adtof_recovery_iteration(object())  # type: ignore[arg-type]


class ADTOFSupervisorBackoffPolicyTests(unittest.TestCase):
    """Prove bounded backoff, recovery reset, and visible fatal exit behavior."""

    def test_retryable_failure_uses_capped_exponential_delays(self) -> None:
        """The zero-jitter sequence is 1, 2, 4, 8, 16, then capped at 30."""

        state = ADTOFSupervisorBackoffState()
        expected_delays = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0)
        expected_streaks = (1, 2, 3, 4, 5, 6, 6)
        for expected_delay, expected_streak in zip(expected_delays, expected_streaks, strict=True):
            decision = next_adtof_supervisor_decision(
                state,
                ADTOFSupervisorEvent.RETRYABLE_FAILURE,
                jitter_fraction=0.0,
            )
            self.assertEqual(decision.action, ADTOFSupervisorAction.RETRY_AFTER_BACKOFF)
            self.assertEqual(decision.delay_seconds, expected_delay)
            self.assertEqual(decision.next_state.retryable_failure_streak, expected_streak)
            state = decision.next_state

    def test_maximum_jitter_and_streak_remain_within_the_hard_delay_bound(self) -> None:
        """A runtime-provided jitter source cannot produce an unbounded wait."""

        middle_decision = next_adtof_supervisor_decision(
            ADTOFSupervisorBackoffState(retryable_failure_streak=2),
            ADTOFSupervisorEvent.RETRYABLE_FAILURE,
            jitter_fraction=1.0,
        )
        self.assertEqual(middle_decision.delay_seconds, 5.0)

        capped_decision = next_adtof_supervisor_decision(
            ADTOFSupervisorBackoffState(retryable_failure_streak=6),
            ADTOFSupervisorEvent.RETRYABLE_FAILURE,
            jitter_fraction=1.0,
        )
        self.assertEqual(capped_decision.delay_seconds, DEFAULT_ADTOF_RETRY_MAX_DELAY_SECONDS)

    def test_normal_idle_or_progress_resets_previous_failure_state(self) -> None:
        """A normal boundary restores the shortest healthy-poll behavior."""

        failed_state = ADTOFSupervisorBackoffState(retryable_failure_streak=4)
        idle_decision = next_adtof_supervisor_decision(
            failed_state,
            ADTOFSupervisorEvent.ITERATION_IDLE,
        )
        self.assertEqual(idle_decision.action, ADTOFSupervisorAction.WAIT_IDLE)
        self.assertEqual(idle_decision.delay_seconds, DEFAULT_ADTOF_IDLE_DELAY_SECONDS)
        self.assertEqual(idle_decision.next_state.retryable_failure_streak, 0)

        progress_decision = next_adtof_supervisor_decision(
            failed_state,
            ADTOFSupervisorEvent.ITERATION_PROGRESS,
        )
        self.assertEqual(progress_decision.action, ADTOFSupervisorAction.CHECK_IMMEDIATELY)
        self.assertEqual(progress_decision.delay_seconds, 0.0)
        self.assertEqual(progress_decision.next_state.retryable_failure_streak, 0)

    def test_fatal_configuration_exits_immediately_without_retry_state(self) -> None:
        """Bad static configuration cannot be silently retried forever."""

        decision = next_adtof_supervisor_decision(
            ADTOFSupervisorBackoffState(retryable_failure_streak=3),
            ADTOFSupervisorEvent.FATAL_CONFIGURATION,
        )
        self.assertEqual(decision.action, ADTOFSupervisorAction.EXIT_FATAL)
        self.assertEqual(decision.delay_seconds, 0.0)
        self.assertEqual(decision.next_state.retryable_failure_streak, 0)

    def test_invalid_policy_inputs_are_rejected_before_a_delay_is_planned(self) -> None:
        """Invalid state/event/jitter cannot silently become a runtime wait."""

        with self.assertRaises(ValueError):
            ADTOFSupervisorBackoffState(retryable_failure_streak=7)
        with self.assertRaises(TypeError):
            next_adtof_supervisor_decision(
                object(),  # type: ignore[arg-type]
                ADTOFSupervisorEvent.ITERATION_IDLE,
            )
        with self.assertRaises(TypeError):
            next_adtof_supervisor_decision(
                ADTOFSupervisorBackoffState(),
                "retryable_failure",  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            next_adtof_supervisor_decision(
                ADTOFSupervisorBackoffState(),
                ADTOFSupervisorEvent.RETRYABLE_FAILURE,
                jitter_fraction=1.1,
            )
        with self.assertRaises(ValueError):
            ADTOFSupervisorDecision(
                action=ADTOFSupervisorAction.CHECK_IMMEDIATELY,
                delay_seconds=DEFAULT_ADTOF_IDLE_DELAY_SECONDS,
                next_state=ADTOFSupervisorBackoffState(),
            )


if __name__ == "__main__":
    unittest.main()
