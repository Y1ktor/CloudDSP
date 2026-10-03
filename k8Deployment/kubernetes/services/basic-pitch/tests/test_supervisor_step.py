"""Unit tests for one Basic Pitch fair-iteration supervisor decision step.

The fair iteration is patched at its public seam. These tests do not poll
RabbitMQ, open PostgreSQL/MinIO, run Basic Pitch, sleep, reconnect, loop, build
an image, or change Kubernetes; they prove only state and decision handoff.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.messaging.amqp_connection import BasicPitchAMQPConfigurationError, BasicPitchAMQPConnectionUnavailable
from app.runtime.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchClaimedTaskExecutionOutcome,
)
from app.runtime.receive_execute_once import BasicPitchWorkerIterationOutcome, BasicPitchWorkerIterationResult
from app.artifacts.stem_object import BasicPitchStemStorageUnavailable
from app.runtime.supervisor_backoff import BasicPitchSupervisorAction, BasicPitchSupervisorBackoffState, BasicPitchSupervisorEvent
from app.runtime.supervisor_step import (
    BasicPitchSupervisorStepState,
    run_one_basic_pitch_supervisor_step,
)
from app.runtime.work_schedule import BasicPitchWorkScheduleState, BasicPitchWorkSource
from app.runtime.work_source_iteration import BasicPitchFairWorkIterationOutcome, BasicPitchFairWorkIterationResult


def progress_iteration() -> BasicPitchFairWorkIterationResult:
    """Return one compact normal broker result with recovery next in rotation."""

    return BasicPitchFairWorkIterationResult(
        outcome=BasicPitchFairWorkIterationOutcome.PROGRESS,
        next_state=BasicPitchWorkScheduleState(
            next_source=BasicPitchWorkSource.DUE_RETRY_RECOVERY,
        ),
        attempted_sources=(BasicPitchWorkSource.RABBITMQ_DELIVERY,),
        broker_result=BasicPitchWorkerIterationResult(
            outcome=BasicPitchWorkerIterationOutcome.EXECUTED,
            execution=BasicPitchClaimedTaskExecution(
                outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
            ),
        ),
    )


def idle_iteration() -> BasicPitchFairWorkIterationResult:
    """Return the only valid normal result that permits a short idle delay."""

    return BasicPitchFairWorkIterationResult(
        outcome=BasicPitchFairWorkIterationOutcome.IDLE,
        next_state=BasicPitchWorkScheduleState(),
        attempted_sources=(
            BasicPitchWorkSource.RABBITMQ_DELIVERY,
            BasicPitchWorkSource.DUE_RETRY_RECOVERY,
        ),
    )


class BasicPitchSupervisorStepTests(unittest.TestCase):
    """Prove fair state and bounded backoff state advance only on reviewed facts."""

    @patch("app.runtime.supervisor_step.run_one_fair_basic_pitch_work_iteration")
    def test_normal_progress_advances_fair_state_and_checks_immediately(self, run_iteration) -> None:
        """A completed normal iteration resets prior local failure backoff."""

        iteration = progress_iteration()
        run_iteration.return_value = iteration
        state = BasicPitchSupervisorStepState(
            backoff_state=BasicPitchSupervisorBackoffState(retryable_failure_streak=3),
        )
        database = MagicMock()
        storage_client = MagicMock()

        step = run_one_basic_pitch_supervisor_step(
            MagicMock(),
            state=state,
            database=database,
            storage_client=storage_client,
            work_directory=MagicMock(),
            process_timeout_seconds=120,
            jitter_fraction=0.5,
        )

        self.assertEqual(step.event, BasicPitchSupervisorEvent.ITERATION_PROGRESS)
        self.assertEqual(step.decision.action, BasicPitchSupervisorAction.CHECK_IMMEDIATELY)
        self.assertIs(step.iteration, iteration)
        self.assertEqual(step.next_state.schedule_state, iteration.next_state)
        self.assertEqual(step.next_state.backoff_state.retryable_failure_streak, 0)
        run_iteration.assert_called_once_with(
            unittest.mock.ANY,
            schedule_state=state.schedule_state,
            database=database,
            storage_client=storage_client,
            work_directory=unittest.mock.ANY,
            process_timeout_seconds=120,
            process_runner=None,
        )

    @patch("app.runtime.supervisor_step.run_one_fair_basic_pitch_work_iteration")
    def test_two_source_idle_preserves_fair_state_and_uses_existing_idle_wait(self, run_iteration) -> None:
        """A one-second wait happens only after both normal sources were checked."""

        iteration = idle_iteration()
        run_iteration.return_value = iteration

        step = run_one_basic_pitch_supervisor_step(
            MagicMock(),
            state=BasicPitchSupervisorStepState(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=MagicMock(),
        )

        self.assertEqual(step.event, BasicPitchSupervisorEvent.ITERATION_IDLE)
        self.assertEqual(step.decision.action, BasicPitchSupervisorAction.WAIT_IDLE)
        self.assertEqual(step.next_state.schedule_state, iteration.next_state)

    @patch("app.runtime.supervisor_step.run_one_fair_basic_pitch_work_iteration")
    def test_retryable_runtime_fault_preserves_fair_preference_and_increments_backoff(
        self, run_iteration
    ) -> None:
        """An incomplete source attempt must not pretend it was normal progress."""

        failure = BasicPitchAMQPConnectionUnavailable("safe test broker outage")
        run_iteration.side_effect = failure
        state = BasicPitchSupervisorStepState(
            schedule_state=BasicPitchWorkScheduleState(
                next_source=BasicPitchWorkSource.DUE_RETRY_RECOVERY,
            ),
        )

        step = run_one_basic_pitch_supervisor_step(
            MagicMock(),
            state=state,
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=MagicMock(),
            jitter_fraction=0.0,
        )

        self.assertEqual(step.event, BasicPitchSupervisorEvent.RETRYABLE_FAILURE)
        self.assertEqual(step.decision.action, BasicPitchSupervisorAction.RETRY_AFTER_BACKOFF)
        self.assertIsNone(step.iteration)
        self.assertEqual(step.next_state.schedule_state, state.schedule_state)
        self.assertEqual(step.next_state.backoff_state.retryable_failure_streak, 1)

    @patch("app.runtime.supervisor_step.run_one_fair_basic_pitch_work_iteration")
    def test_fatal_configuration_returns_exit_without_iteration_evidence(self, run_iteration) -> None:
        """The later loop can exit visibly instead of silently retrying a bad Secret."""

        run_iteration.side_effect = BasicPitchAMQPConfigurationError("safe test configuration")

        step = run_one_basic_pitch_supervisor_step(
            MagicMock(),
            state=BasicPitchSupervisorStepState(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=MagicMock(),
        )

        self.assertEqual(step.event, BasicPitchSupervisorEvent.FATAL_CONFIGURATION)
        self.assertEqual(step.decision.action, BasicPitchSupervisorAction.EXIT_FATAL)
        self.assertIsNone(step.iteration)

    @patch("app.runtime.supervisor_step.run_one_fair_basic_pitch_work_iteration")
    def test_unclassified_task_failure_propagates_unchanged(self, run_iteration) -> None:
        """A generic supervisor decision must not replace task-specific handling."""

        failure = BasicPitchStemStorageUnavailable("safe test stem outage")
        run_iteration.side_effect = failure

        with self.assertRaises(BasicPitchStemStorageUnavailable) as raised:
            run_one_basic_pitch_supervisor_step(
                MagicMock(),
                state=BasicPitchSupervisorStepState(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=MagicMock(),
            )

        self.assertIs(raised.exception, failure)


if __name__ == "__main__":
    unittest.main()
