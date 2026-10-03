"""Unit tests for one cadence-aware Demucs supervisor decision step.

The worker cycle is patched at its public seam. Tests never poll RabbitMQ,
connect to PostgreSQL/MinIO, run FFprobe/Demucs, sleep, reconnect, loop, build
an image, or create Kubernetes resources.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import ANY, MagicMock, patch

from app.messaging.amqp_connection import DemucsAMQPConfigurationError, DemucsAMQPConnectionUnavailable
from app.runtime.pre_model_failure_runtime import DemucsOneTaskExecution, DemucsOneTaskExecutionOutcome
from app.runtime.receive_execute_once import DemucsWorkerIterationOutcome, DemucsWorkerIterationResult
from app.runtime.recovery_cadence import DemucsWorkerCadenceAction, DemucsWorkerCadenceState
from app.runtime.recovery_execute_once import DemucsRecoveryIterationOutcome, DemucsRecoveryIterationResult
from app.artifacts.source_object import DemucsSourceStorageProtocolError
from app.runtime.supervisor_backoff import DemucsSupervisorAction, DemucsSupervisorBackoffState, DemucsSupervisorEvent
from app.runtime.supervisor_step import DemucsSupervisorStepState, run_one_demucs_supervisor_step
from app.runtime.worker_cycle import DemucsWorkerCycleResult


def execution() -> DemucsOneTaskExecution:
    """Return compact ownership-loss evidence without model execution."""

    return DemucsOneTaskExecution(DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)


def recovery_cycle(result: DemucsRecoveryIterationResult) -> DemucsWorkerCycleResult:
    """Build a valid recovery result that schedules normal work next."""

    return DemucsWorkerCycleResult(
        action=DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN,
        next_state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION),
        recovery_iteration=result,
    )


def normal_cycle(result: DemucsWorkerIterationResult) -> DemucsWorkerCycleResult:
    """Build a valid normal result that schedules recovery next."""

    return DemucsWorkerCycleResult(
        action=DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION,
        next_state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN),
        normal_iteration=result,
    )


class DemucsSupervisorStepTests(unittest.TestCase):
    """Prove backoff/fairness state advances only after a complete cycle."""

    @patch("app.runtime.supervisor_step.run_one_demucs_worker_cycle")
    def test_recovery_idle_checks_normal_queue_immediately_and_advances_cadence(self, run_cycle) -> None:
        """Recovery idle is not broker idle, so it never adds an idle delay."""

        cycle = recovery_cycle(DemucsRecoveryIterationResult(DemucsRecoveryIterationOutcome.IDLE))
        run_cycle.return_value = cycle
        state = DemucsSupervisorStepState(
            backoff_state=DemucsSupervisorBackoffState(retryable_failure_streak=3),
        )
        channel = MagicMock()
        database = MagicMock()
        source_client = MagicMock()
        artifact_client = MagicMock()

        step = run_one_demucs_supervisor_step(
            channel,
            state=state,
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/worker-scratch"),
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
            jitter_fraction=0.5,
        )

        self.assertEqual(step.event, DemucsSupervisorEvent.ITERATION_PROGRESS)
        self.assertEqual(step.decision.action, DemucsSupervisorAction.CHECK_IMMEDIATELY)
        self.assertIs(step.cycle, cycle)
        self.assertEqual(step.next_state.backoff_state.retryable_failure_streak, 0)
        self.assertEqual(
            step.next_state.cadence_state.next_action,
            DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION,
        )
        run_cycle.assert_called_once_with(
            channel,
            state=state.cadence_state,
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/worker-scratch"),
            ffprobe_runner=None,
            demucs_runner=None,
            uploader=None,
            event_id_factory=ANY,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
        )

    @patch("app.runtime.supervisor_step.run_one_demucs_worker_cycle")
    def test_normal_amqp_idle_uses_short_wait_and_advances_to_recovery(self, run_cycle) -> None:
        """Only normal empty broker polling receives the idle decision."""

        cycle = normal_cycle(DemucsWorkerIterationResult(DemucsWorkerIterationOutcome.IDLE))
        run_cycle.return_value = cycle
        state = DemucsSupervisorStepState(
            cadence_state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION),
        )

        step = run_one_demucs_supervisor_step(
            MagicMock(),
            state=state,
            database=MagicMock(),
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(step.event, DemucsSupervisorEvent.ITERATION_IDLE)
        self.assertEqual(step.decision.action, DemucsSupervisorAction.WAIT_IDLE)
        self.assertIs(step.cycle, cycle)
        self.assertEqual(
            step.next_state.cadence_state.next_action,
            DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN,
        )

    @patch("app.runtime.supervisor_step.run_one_demucs_worker_cycle")
    def test_retryable_fault_preserves_prior_cadence_without_cycle_evidence(self, run_cycle) -> None:
        """An outage retries the same selected recovery action after backoff."""

        run_cycle.side_effect = DemucsAMQPConnectionUnavailable("safe test broker outage")
        state = DemucsSupervisorStepState(
            backoff_state=DemucsSupervisorBackoffState(retryable_failure_streak=1),
            cadence_state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN),
        )

        step = run_one_demucs_supervisor_step(
            MagicMock(),
            state=state,
            database=MagicMock(),
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            jitter_fraction=0.0,
        )

        self.assertEqual(step.event, DemucsSupervisorEvent.RETRYABLE_FAILURE)
        self.assertEqual(step.decision.action, DemucsSupervisorAction.RETRY_AFTER_BACKOFF)
        self.assertIsNone(step.cycle)
        self.assertEqual(step.next_state.backoff_state.retryable_failure_streak, 2)
        self.assertEqual(step.next_state.cadence_state, state.cadence_state)

    @patch("app.runtime.supervisor_step.run_one_demucs_worker_cycle")
    def test_fatal_configuration_preserves_normal_cadence_without_cycle_evidence(self, run_cycle) -> None:
        """Static configuration exits rather than silently skipping a branch."""

        run_cycle.side_effect = DemucsAMQPConfigurationError("safe test configuration")
        state = DemucsSupervisorStepState(
            cadence_state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION),
        )

        step = run_one_demucs_supervisor_step(
            MagicMock(),
            state=state,
            database=MagicMock(),
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(step.event, DemucsSupervisorEvent.FATAL_CONFIGURATION)
        self.assertEqual(step.decision.action, DemucsSupervisorAction.EXIT_FATAL)
        self.assertIsNone(step.cycle)
        self.assertEqual(step.next_state.cadence_state, state.cadence_state)

    @patch("app.runtime.supervisor_step.run_one_demucs_worker_cycle")
    def test_unclassified_task_error_propagates_unchanged(self, run_cycle) -> None:
        """Generic supervision cannot override task-integrity handling."""

        failure = DemucsSourceStorageProtocolError("safe test metadata mismatch")
        run_cycle.side_effect = failure

        with self.assertRaises(DemucsSourceStorageProtocolError) as raised:
            run_one_demucs_supervisor_step(
                MagicMock(),
                state=DemucsSupervisorStepState(),
                database=MagicMock(),
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )

        self.assertIs(raised.exception, failure)


if __name__ == "__main__":
    unittest.main()
