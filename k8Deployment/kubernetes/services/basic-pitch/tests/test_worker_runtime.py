"""Unit tests for the Basic Pitch worker runtime loop composition.

The broker factory, channel setup, and supervisor step are patched at their
public seams. These tests run no real loop forever, sleep, signal handler,
RabbitMQ/PostgreSQL/MinIO connection, model process, Docker action, or
Kubernetes resource; they prove lifecycle and exit ordering only.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.amqp_connection import DEFAULT_BASIC_PITCH_AMQP_HOST, BasicPitchAMQPSettings
from app.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchClaimedTaskExecutionOutcome,
)
from app.receive_execute_once import BasicPitchWorkerIterationOutcome, BasicPitchWorkerIterationResult
from app.supervisor_backoff import (
    DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS,
    BasicPitchSupervisorAction,
    BasicPitchSupervisorBackoffState,
    BasicPitchSupervisorDecision,
    BasicPitchSupervisorEvent,
)
from app.supervisor_step import (
    BasicPitchSupervisorStepResult,
    BasicPitchSupervisorStepState,
)
from app.work_schedule import BasicPitchWorkScheduleState, BasicPitchWorkSource
from app.work_source_iteration import BasicPitchFairWorkIterationOutcome, BasicPitchFairWorkIterationResult
from app.worker_runtime import BasicPitchWorkerExitReason, run_basic_pitch_worker_runtime


def settings() -> BasicPitchAMQPSettings:
    """Return reviewed synthetic settings; opening is patched in these tests."""

    return BasicPitchAMQPSettings(
        host=DEFAULT_BASIC_PITCH_AMQP_HOST,
        port=5672,
        username="clouddsp-basic-pitch",
        password="not-a-real-password",
    )


def progress_step() -> BasicPitchSupervisorStepResult:
    """Return one immediate normal step whose next fair source is recovery."""

    next_state = BasicPitchSupervisorStepState(
        schedule_state=BasicPitchWorkScheduleState(
            next_source=BasicPitchWorkSource.DUE_RETRY_RECOVERY,
        ),
        backoff_state=BasicPitchSupervisorBackoffState(),
    )
    iteration = BasicPitchFairWorkIterationResult(
        outcome=BasicPitchFairWorkIterationOutcome.PROGRESS,
        next_state=next_state.schedule_state,
        attempted_sources=(BasicPitchWorkSource.RABBITMQ_DELIVERY,),
        broker_result=BasicPitchWorkerIterationResult(
            outcome=BasicPitchWorkerIterationOutcome.EXECUTED,
            execution=BasicPitchClaimedTaskExecution(
                outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
            ),
        ),
    )
    return BasicPitchSupervisorStepResult(
        event=BasicPitchSupervisorEvent.ITERATION_PROGRESS,
        decision=BasicPitchSupervisorDecision(
            action=BasicPitchSupervisorAction.CHECK_IMMEDIATELY,
            delay_seconds=0.0,
            next_state=next_state.backoff_state,
        ),
        next_state=next_state,
        iteration=iteration,
    )


def idle_step() -> BasicPitchSupervisorStepResult:
    """Return one two-source idle step whose action wait will receive shutdown."""

    next_state = BasicPitchSupervisorStepState()
    iteration = BasicPitchFairWorkIterationResult(
        outcome=BasicPitchFairWorkIterationOutcome.IDLE,
        next_state=next_state.schedule_state,
        attempted_sources=(
            BasicPitchWorkSource.DUE_RETRY_RECOVERY,
            BasicPitchWorkSource.RABBITMQ_DELIVERY,
        ),
    )
    return BasicPitchSupervisorStepResult(
        event=BasicPitchSupervisorEvent.ITERATION_IDLE,
        decision=BasicPitchSupervisorDecision(
            action=BasicPitchSupervisorAction.WAIT_IDLE,
            delay_seconds=DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS,
            next_state=next_state.backoff_state,
        ),
        next_state=next_state,
        iteration=iteration,
    )


def fatal_step() -> BasicPitchSupervisorStepResult:
    """Return one fatal supervisor decision without claiming normal work."""

    state = BasicPitchSupervisorStepState()
    return BasicPitchSupervisorStepResult(
        event=BasicPitchSupervisorEvent.FATAL_CONFIGURATION,
        decision=BasicPitchSupervisorDecision(
            action=BasicPitchSupervisorAction.EXIT_FATAL,
            delay_seconds=0.0,
            next_state=state.backoff_state,
        ),
        next_state=state,
    )


class SequencedShutdownWaiter:
    """Return scripted event-wait values and retain zero/timeout checks."""

    def __init__(self, *results: bool) -> None:
        self._results = iter(results)
        self.delays: list[float] = []

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        self.delays.append(timeout_seconds)
        return next(self._results)


class BasicPitchWorkerRuntimeTests(unittest.TestCase):
    """Prove one connection closes for every normal or exceptional loop path."""

    @patch("app.worker_runtime.run_one_basic_pitch_supervisor_step")
    @patch("app.worker_runtime.configure_basic_pitch_rabbitmq_channel")
    @patch("app.worker_runtime.open_basic_pitch_rabbitmq_connection")
    def test_runs_steps_until_interruptible_idle_wait_requests_shutdown(
        self, open_connection, configure_channel, run_step
    ) -> None:
        """Progress updates state; shutdown exits after its current reviewed decision."""

        connection = MagicMock()
        channel = MagicMock()
        connection.channel.return_value = channel
        open_connection.return_value = connection
        run_step.side_effect = (progress_step(), idle_step())
        # before open, before first step, before second step, then idle wait
        waiter = SequencedShutdownWaiter(False, False, False, True)
        database = MagicMock()
        storage_client = MagicMock()

        result = run_basic_pitch_worker_runtime(
            amqp_settings=settings(),
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
            jitter_fraction=0.0,
        )

        self.assertEqual(result.reason, BasicPitchWorkerExitReason.SHUTDOWN_REQUESTED)
        self.assertEqual(result.completed_steps, 2)
        self.assertEqual(
            result.final_state.schedule_state,
            idle_step().next_state.schedule_state,
        )
        open_connection.assert_called_once_with(settings())
        configure_channel.assert_called_once_with(channel, settings=settings())
        self.assertEqual(waiter.delays, [0.0, 0.0, 0.0, DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS])
        self.assertEqual(run_step.call_count, 2)
        self.assertEqual(run_step.call_args_list[1].kwargs["state"], progress_step().next_state)
        connection.close.assert_called_once_with()

    @patch("app.worker_runtime.run_one_basic_pitch_supervisor_step")
    @patch("app.worker_runtime.configure_basic_pitch_rabbitmq_channel")
    @patch("app.worker_runtime.open_basic_pitch_rabbitmq_connection")
    def test_fatal_step_returns_visible_fatal_exit_and_closes_connection(
        self, open_connection, configure_channel, run_step
    ) -> None:
        """The outer process can choose a nonzero exit after safe broker cleanup."""

        connection = MagicMock()
        connection.channel.return_value = MagicMock()
        open_connection.return_value = connection
        run_step.return_value = fatal_step()
        waiter = SequencedShutdownWaiter(False, False)

        result = run_basic_pitch_worker_runtime(
            amqp_settings=settings(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.reason, BasicPitchWorkerExitReason.FATAL_CONFIGURATION)
        self.assertEqual(result.completed_steps, 1)
        self.assertEqual(waiter.delays, [0.0, 0.0])
        connection.close.assert_called_once_with()

    @patch("app.worker_runtime.run_one_basic_pitch_supervisor_step")
    @patch("app.worker_runtime.configure_basic_pitch_rabbitmq_channel")
    @patch("app.worker_runtime.open_basic_pitch_rabbitmq_connection")
    def test_shutdown_before_open_avoids_broker_resource_creation(
        self, open_connection, configure_channel, run_step
    ) -> None:
        """A terminating Pod must not create a fresh connection just to close it."""

        waiter = SequencedShutdownWaiter(True)

        result = run_basic_pitch_worker_runtime(
            amqp_settings=settings(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.reason, BasicPitchWorkerExitReason.SHUTDOWN_REQUESTED)
        self.assertEqual(result.completed_steps, 0)
        open_connection.assert_not_called()
        configure_channel.assert_not_called()
        run_step.assert_not_called()

    @patch("app.worker_runtime.run_one_basic_pitch_supervisor_step")
    @patch("app.worker_runtime.configure_basic_pitch_rabbitmq_channel")
    @patch("app.worker_runtime.open_basic_pitch_rabbitmq_connection")
    def test_unclassified_step_failure_propagates_after_connection_cleanup(
        self, open_connection, configure_channel, run_step
    ) -> None:
        """The loop never turns incomplete task work into a normal exit result."""

        connection = MagicMock()
        connection.channel.return_value = MagicMock()
        open_connection.return_value = connection
        failure = RuntimeError("private test-only task failure")
        run_step.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            run_basic_pitch_worker_runtime(
                amqp_settings=settings(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
                shutdown_waiter=SequencedShutdownWaiter(False, False),
            )

        self.assertIs(raised.exception, failure)
        connection.close.assert_called_once_with()

    @patch("app.worker_runtime.run_one_basic_pitch_supervisor_step")
    @patch("app.worker_runtime.configure_basic_pitch_rabbitmq_channel")
    @patch("app.worker_runtime.open_basic_pitch_rabbitmq_connection")
    def test_channel_setup_failure_closes_the_open_connection_before_propagating(
        self, open_connection, configure_channel, run_step
    ) -> None:
        """A topology/readiness fault cannot leak a connection outside this runtime scope."""

        connection = MagicMock()
        connection.channel.return_value = MagicMock()
        open_connection.return_value = connection
        failure = RuntimeError("private test-only channel setup failure")
        configure_channel.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            run_basic_pitch_worker_runtime(
                amqp_settings=settings(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
                shutdown_waiter=SequencedShutdownWaiter(False),
            )

        self.assertIs(raised.exception, failure)
        run_step.assert_not_called()
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
