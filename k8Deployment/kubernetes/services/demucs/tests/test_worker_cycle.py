"""Unit tests for one cadence-selected Demucs worker-cycle composition.

Normal and recovery branches are mocked at their public seams. These tests do
not connect to PostgreSQL, MinIO, RabbitMQ, run Demucs, sleep, loop, or create
Kubernetes resources.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import ANY, MagicMock, patch

from app.pre_model_failure_runtime import DemucsOneTaskExecution, DemucsOneTaskExecutionOutcome
from app.receive_execute_once import DemucsWorkerIterationOutcome, DemucsWorkerIterationResult
from app.recovery_cadence import DemucsWorkerCadenceAction, DemucsWorkerCadenceState
from app.recovery_execute_once import DemucsRecoveryIterationOutcome, DemucsRecoveryIterationResult
from app.worker_cycle import DemucsWorkerCycleResult, run_one_demucs_worker_cycle


def execution() -> DemucsOneTaskExecution:
    """Return compact task evidence with no external test action."""

    return DemucsOneTaskExecution(DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)


class DemucsWorkerCycleTests(unittest.TestCase):
    """Prove each bounded cycle invokes only the branch selected by state."""

    @patch("app.worker_cycle.receive_and_execute_demucs_once")
    @patch("app.worker_cycle.recover_and_execute_demucs_once")
    def test_recovery_selected_cycle_never_passes_channel_to_normal_amqp_work(
        self,
        recover,
        normal,
    ) -> None:
        """A fresh Pod's first cycle performs delivery-free recovery only."""

        recovery_iteration = DemucsRecoveryIterationResult(DemucsRecoveryIterationOutcome.IDLE)
        recover.return_value = recovery_iteration
        channel = MagicMock()
        database = MagicMock()
        source_client = MagicMock()
        artifact_client = MagicMock()

        result = run_one_demucs_worker_cycle(
            channel,
            state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN),
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(result.action, DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN)
        self.assertIs(result.recovery_iteration, recovery_iteration)
        self.assertIsNone(result.normal_iteration)
        self.assertEqual(result.next_state.next_action, DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION)
        recover.assert_called_once_with(
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/worker-scratch"),
            ffprobe_runner=None,
            demucs_runner=None,
            uploader=None,
            event_id_factory=ANY,
            # Defaults stay owned by the one-attempt runtime policy; the
            # cycle forwards them unchanged to either selected branch.
            pre_model_retry_after_seconds=30,
            running_retry_after_seconds=30,
        )
        normal.assert_not_called()

    @patch("app.worker_cycle.receive_and_execute_demucs_once")
    @patch("app.worker_cycle.recover_and_execute_demucs_once")
    def test_normal_selected_cycle_passes_channel_only_to_normal_branch(self, recover, normal) -> None:
        """One normal AMQP result always makes recovery the next cycle."""

        normal_iteration = DemucsWorkerIterationResult(
            DemucsWorkerIterationOutcome.EXECUTED,
            execution=execution(),
        )
        normal.return_value = normal_iteration
        channel = MagicMock()
        database = MagicMock()
        source_client = MagicMock()
        artifact_client = MagicMock()
        demucs_runner = MagicMock()

        result = run_one_demucs_worker_cycle(
            channel,
            state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION),
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/worker-scratch"),
            demucs_runner=demucs_runner,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
        )

        self.assertEqual(result.action, DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION)
        self.assertIs(result.normal_iteration, normal_iteration)
        self.assertIsNone(result.recovery_iteration)
        self.assertEqual(result.next_state.next_action, DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN)
        normal.assert_called_once_with(
            channel,
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/worker-scratch"),
            ffprobe_runner=None,
            demucs_runner=demucs_runner,
            uploader=None,
            event_id_factory=ANY,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
        )
        recover.assert_not_called()

    @patch("app.worker_cycle.receive_and_execute_demucs_once")
    @patch("app.worker_cycle.recover_and_execute_demucs_once")
    def test_failed_or_forged_selected_branch_does_not_create_a_cycle_result(self, recover, normal) -> None:
        """A supervisor can retry unchanged state instead of skipping the action."""

        failure = RuntimeError("test-only recovery failure")
        recover.side_effect = failure
        state = DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN)
        with self.assertRaises(RuntimeError) as raised:
            run_one_demucs_worker_cycle(
                MagicMock(),
                state=state,
                database=MagicMock(),
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )
        self.assertIs(raised.exception, failure)
        normal.assert_not_called()

        recover.reset_mock(return_value=True, side_effect=True)
        normal.return_value = object()
        with self.assertRaisesRegex(TypeError, "normal iteration result is invalid"):
            run_one_demucs_worker_cycle(
                MagicMock(),
                state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION),
                database=MagicMock(),
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )


class DemucsWorkerCycleResultTests(unittest.TestCase):
    """Prove a cycle cannot claim the wrong branch evidence or next action."""

    def test_result_requires_matching_branch_evidence_and_next_action(self) -> None:
        """Normal and recovery facts remain mutually exclusive and ordered."""

        with self.assertRaises(ValueError):
            DemucsWorkerCycleResult(
                action=DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN,
                next_state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN),
                recovery_iteration=DemucsRecoveryIterationResult(DemucsRecoveryIterationOutcome.IDLE),
            )

        with self.assertRaises(ValueError):
            DemucsWorkerCycleResult(
                action=DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION,
                next_state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN),
                normal_iteration=DemucsWorkerIterationResult(DemucsWorkerIterationOutcome.IDLE),
                recovery_iteration=DemucsRecoveryIterationResult(DemucsRecoveryIterationOutcome.IDLE),
            )


if __name__ == "__main__":
    unittest.main()
