"""Unit tests for one cadence-selected ADTOF worker-cycle composition.

Normal and recovery branches are mocked at their public seams. These tests do
not connect to PostgreSQL/MinIO/RabbitMQ, run ADTOF, sleep, loop, or create
Kubernetes resources.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.claimed_task_success import ADTOFClaimedTaskSuccess, ADTOFClaimedTaskSuccessOutcome
from app.recovery_cadence import ADTOFWorkerCadenceAction, ADTOFWorkerCadenceState
from app.recovery_execute_once import ADTOFRecoveryIterationOutcome, ADTOFRecoveryIterationResult
from app.receive_execute_once import ADTOFWorkerIterationOutcome, ADTOFWorkerIterationResult
from app.worker_cycle import ADTOFWorkerCycleResult, run_one_adtof_worker_cycle


def execution() -> ADTOFClaimedTaskSuccess:
    """Return compact downstream evidence with no external test work."""

    return ADTOFClaimedTaskSuccess(ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST)


class ADTOFWorkerCycleTests(unittest.TestCase):
    """Prove each cycle invokes exactly the branch selected by its state."""

    @patch("app.worker_cycle.receive_and_execute_adtof_once")
    @patch("app.worker_cycle.recover_and_execute_adtof_once")
    def test_recovery_selected_cycle_never_passes_channel_to_normal_amqp_work(
        self,
        recover,
        normal,
    ) -> None:
        """A fresh Pod's first cycle is delivery-free recovery, not broker I/O."""

        recovery_iteration = ADTOFRecoveryIterationResult(ADTOFRecoveryIterationOutcome.IDLE)
        recover.return_value = recovery_iteration
        channel = MagicMock()
        database = MagicMock()
        storage_client = MagicMock()

        result = run_one_adtof_worker_cycle(
            channel,
            state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN),
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(result.action, ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN)
        self.assertIs(result.recovery_iteration, recovery_iteration)
        self.assertIsNone(result.normal_iteration)
        self.assertEqual(result.next_state.next_action, ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION)
        recover.assert_called_once_with(
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=10 * 60,
            process_runner=None,
        )
        normal.assert_not_called()

    @patch("app.worker_cycle.receive_and_execute_adtof_once")
    @patch("app.worker_cycle.recover_and_execute_adtof_once")
    def test_normal_selected_cycle_passes_channel_only_to_normal_branch(self, recover, normal) -> None:
        """One normal AMQP result always advances the next cycle to recovery."""

        normal_iteration = ADTOFWorkerIterationResult(
            ADTOFWorkerIterationOutcome.EXECUTED,
            execution=execution(),
        )
        normal.return_value = normal_iteration
        channel = MagicMock()
        database = MagicMock()
        storage_client = MagicMock()
        runner = MagicMock()

        result = run_one_adtof_worker_cycle(
            channel,
            state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION),
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

        self.assertEqual(result.action, ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION)
        self.assertIs(result.normal_iteration, normal_iteration)
        self.assertIsNone(result.recovery_iteration)
        self.assertEqual(result.next_state.next_action, ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN)
        normal.assert_called_once_with(
            channel,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )
        recover.assert_not_called()

    @patch("app.worker_cycle.receive_and_execute_adtof_once")
    @patch("app.worker_cycle.recover_and_execute_adtof_once")
    def test_failed_or_forged_selected_branch_does_not_create_a_cycle_result(self, recover, normal) -> None:
        """The supervisor can retry the unchanged state rather than skip an action."""

        failure = RuntimeError("private recovery failure")
        recover.side_effect = failure
        state = ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN)
        with self.assertRaises(RuntimeError) as raised:
            run_one_adtof_worker_cycle(
                MagicMock(),
                state=state,
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )
        self.assertIs(raised.exception, failure)
        normal.assert_not_called()

        recover.reset_mock(return_value=True, side_effect=True)
        normal.return_value = object()
        with self.assertRaisesRegex(TypeError, "normal iteration result is invalid"):
            run_one_adtof_worker_cycle(
                MagicMock(),
                state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )


class ADTOFWorkerCycleResultTests(unittest.TestCase):
    """Prove a returned cycle cannot claim the wrong branch or next action."""

    def test_result_requires_matching_branch_evidence_and_next_action(self) -> None:
        """Compact normal/recovery outcomes remain mutually exclusive."""

        with self.assertRaises(ValueError):
            ADTOFWorkerCycleResult(
                action=ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN,
                next_state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN),
                recovery_iteration=ADTOFRecoveryIterationResult(ADTOFRecoveryIterationOutcome.IDLE),
            )

        with self.assertRaises(ValueError):
            ADTOFWorkerCycleResult(
                action=ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION,
                next_state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN),
                normal_iteration=ADTOFWorkerIterationResult(ADTOFWorkerIterationOutcome.IDLE),
                recovery_iteration=ADTOFRecoveryIterationResult(ADTOFRecoveryIterationOutcome.IDLE),
            )


if __name__ == "__main__":
    unittest.main()
