"""Unit tests for one cadence-aware ADTOF supervisor decision step.

The worker cycle is patched at its public seam. These tests do not poll
RabbitMQ, connect to PostgreSQL/MinIO, run ADTOF, sleep, reconnect, loop,
build an image, or create Kubernetes state.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.messaging.amqp_connection import ADTOFAMQPConfigurationError, ADTOFAMQPConnectionUnavailable
from app.runtime.claimed_task_success import ADTOFClaimedTaskSuccess, ADTOFClaimedTaskSuccessOutcome
from app.runtime.recovery_cadence import ADTOFWorkerCadenceAction, ADTOFWorkerCadenceState
from app.runtime.recovery_execute_once import ADTOFRecoveryIterationOutcome, ADTOFRecoveryIterationResult
from app.runtime.receive_execute_once import ADTOFWorkerIterationOutcome, ADTOFWorkerIterationResult
from app.artifacts.stem_object import ADTOFStemStorageProtocolError
from app.runtime.supervisor_backoff import ADTOFSupervisorAction, ADTOFSupervisorBackoffState, ADTOFSupervisorEvent
from app.runtime.supervisor_step import ADTOFSupervisorStepState, run_one_adtof_supervisor_step
from app.runtime.worker_cycle import ADTOFWorkerCycleResult


def execution() -> ADTOFClaimedTaskSuccess:
    """Return compact ownership-loss evidence without running model work."""

    return ADTOFClaimedTaskSuccess(ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST)


def recovery_cycle(result: ADTOFRecoveryIterationResult) -> ADTOFWorkerCycleResult:
    """Return a valid recovery branch result that schedules normal work next."""

    return ADTOFWorkerCycleResult(
        action=ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN,
        next_state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION),
        recovery_iteration=result,
    )


def normal_cycle(result: ADTOFWorkerIterationResult) -> ADTOFWorkerCycleResult:
    """Return a valid normal branch result that schedules recovery next."""

    return ADTOFWorkerCycleResult(
        action=ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION,
        next_state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN),
        normal_iteration=result,
    )


class ADTOFSupervisorStepTests(unittest.TestCase):
    """Prove backoff and fairness-cadence state change only after a cycle result."""

    @patch("app.runtime.supervisor_step.run_one_adtof_worker_cycle")
    def test_recovery_idle_checks_normal_queue_immediately_and_advances_cadence(self, run_cycle) -> None:
        """Recovery idle is not AMQP idle, so it must not add the broker-poll wait."""

        cycle = recovery_cycle(ADTOFRecoveryIterationResult(ADTOFRecoveryIterationOutcome.IDLE))
        run_cycle.return_value = cycle
        state = ADTOFSupervisorStepState(
            backoff_state=ADTOFSupervisorBackoffState(retryable_failure_streak=3),
        )
        database = MagicMock()
        storage_client = MagicMock()

        step = run_one_adtof_supervisor_step(
            MagicMock(),
            state=state,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            jitter_fraction=0.5,
        )

        self.assertEqual(step.event, ADTOFSupervisorEvent.ITERATION_PROGRESS)
        self.assertEqual(step.decision.action, ADTOFSupervisorAction.CHECK_IMMEDIATELY)
        self.assertIs(step.cycle, cycle)
        self.assertEqual(step.next_state.backoff_state.retryable_failure_streak, 0)
        self.assertEqual(
            step.next_state.cadence_state.next_action,
            ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION,
        )
        run_cycle.assert_called_once_with(
            unittest.mock.ANY,
            state=state.cadence_state,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=None,
        )

    @patch("app.runtime.supervisor_step.run_one_adtof_worker_cycle")
    def test_normal_amqp_idle_uses_short_wait_and_advances_to_recovery(self, run_cycle) -> None:
        """Only a normal empty broker poll receives the established idle delay."""

        cycle = normal_cycle(ADTOFWorkerIterationResult(ADTOFWorkerIterationOutcome.IDLE))
        run_cycle.return_value = cycle
        state = ADTOFSupervisorStepState(
            cadence_state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION),
        )

        step = run_one_adtof_supervisor_step(
            MagicMock(),
            state=state,
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(step.event, ADTOFSupervisorEvent.ITERATION_IDLE)
        self.assertEqual(step.decision.action, ADTOFSupervisorAction.WAIT_IDLE)
        self.assertIs(step.cycle, cycle)
        self.assertEqual(
            step.next_state.cadence_state.next_action,
            ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN,
        )

    @patch("app.runtime.supervisor_step.run_one_adtof_worker_cycle")
    def test_retryable_fault_preserves_prior_cadence_without_cycle_evidence(self, run_cycle) -> None:
        """An outage retries the same selected recovery action after backoff."""

        run_cycle.side_effect = ADTOFAMQPConnectionUnavailable("safe test broker outage")
        state = ADTOFSupervisorStepState(
            backoff_state=ADTOFSupervisorBackoffState(retryable_failure_streak=1),
            cadence_state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN),
        )

        step = run_one_adtof_supervisor_step(
            MagicMock(),
            state=state,
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            jitter_fraction=0.0,
        )

        self.assertEqual(step.event, ADTOFSupervisorEvent.RETRYABLE_FAILURE)
        self.assertEqual(step.decision.action, ADTOFSupervisorAction.RETRY_AFTER_BACKOFF)
        self.assertIsNone(step.cycle)
        self.assertEqual(step.next_state.backoff_state.retryable_failure_streak, 2)
        self.assertEqual(step.next_state.cadence_state, state.cadence_state)

    @patch("app.runtime.supervisor_step.run_one_adtof_worker_cycle")
    def test_fatal_configuration_preserves_normal_cadence_without_cycle_evidence(self, run_cycle) -> None:
        """Static configuration failure exits rather than silently skipping a branch."""

        run_cycle.side_effect = ADTOFAMQPConfigurationError("safe test configuration")
        state = ADTOFSupervisorStepState(
            cadence_state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION),
        )

        step = run_one_adtof_supervisor_step(
            MagicMock(),
            state=state,
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(step.event, ADTOFSupervisorEvent.FATAL_CONFIGURATION)
        self.assertEqual(step.decision.action, ADTOFSupervisorAction.EXIT_FATAL)
        self.assertIsNone(step.cycle)
        self.assertEqual(step.next_state.cadence_state, state.cadence_state)

    @patch("app.runtime.supervisor_step.run_one_adtof_worker_cycle")
    def test_unclassified_task_error_propagates_unchanged(self, run_cycle) -> None:
        """A generic supervisor step cannot override task-integrity handling."""

        failure = ADTOFStemStorageProtocolError("safe test metadata mismatch")
        run_cycle.side_effect = failure

        with self.assertRaises(ADTOFStemStorageProtocolError) as raised:
            run_one_adtof_supervisor_step(
                MagicMock(),
                state=ADTOFSupervisorStepState(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )

        self.assertIs(raised.exception, failure)


if __name__ == "__main__":
    unittest.main()
