"""Unit tests for Demucs's shutdown-aware persistent supervisor loop.

The one-step runner is mocked at its public seam. Tests use an in-memory
shutdown Event and never connect to services, wait for timeout, run Demucs,
build an image, or change Kubernetes resources.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.runtime.recovery_cadence import DemucsWorkerCadenceAction, DemucsWorkerCadenceState
from app.runtime.shutdown_event import DemucsShutdownWaiter
from app.runtime.supervisor_action import DemucsSupervisorActionOutcome, DemucsSupervisorActionResult
from app.runtime.supervisor_backoff import (
    DemucsSupervisorAction,
    DemucsSupervisorBackoffState,
    DemucsSupervisorDecision,
    DemucsSupervisorEvent,
)
from app.runtime.supervisor_loop import DemucsSupervisorLoopOutcome, run_demucs_supervisor_until_stop
from app.runtime.supervisor_once import DemucsSupervisorOnceResult
from app.runtime.supervisor_step import DemucsSupervisorStepResult, DemucsSupervisorStepState


def state(streak: int = 0) -> DemucsSupervisorStepState:
    """Return deterministic local recovery-first supervisor state."""

    return DemucsSupervisorStepState(
        backoff_state=DemucsSupervisorBackoffState(streak),
        cadence_state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN),
    )


def once_result(
    outcome: DemucsSupervisorActionOutcome,
    *,
    next_state: DemucsSupervisorStepState,
) -> DemucsSupervisorOnceResult:
    """Return valid runner evidence with matching control/terminal action."""

    if outcome is DemucsSupervisorActionOutcome.EXIT_FATAL:
        event = DemucsSupervisorEvent.FATAL_CONFIGURATION
        action = DemucsSupervisorAction.EXIT_FATAL
        delay = 0.0
        decision_state = DemucsSupervisorBackoffState()
        next_state = DemucsSupervisorStepState(
            backoff_state=decision_state,
            cadence_state=next_state.cadence_state,
        )
    else:
        event = DemucsSupervisorEvent.RETRYABLE_FAILURE
        action = DemucsSupervisorAction.RETRY_AFTER_BACKOFF
        delay = 1.0
        decision_state = next_state.backoff_state

    step = DemucsSupervisorStepResult(
        event=event,
        decision=DemucsSupervisorDecision(
            action=action,
            delay_seconds=delay,
            next_state=decision_state,
        ),
        next_state=next_state,
    )
    return DemucsSupervisorOnceResult(
        step=step,
        action_result=DemucsSupervisorActionResult(outcome=outcome, action=action),
        next_state=next_state,
    )


class DemucsSupervisorLoopTests(unittest.TestCase):
    """Prove repetition occurs only after a confirmed `continue` control fact."""

    @patch("app.runtime.supervisor_loop.run_one_demucs_supervisor_cycle")
    def test_preexisting_shutdown_stops_before_any_worker_cycle(self, run_once) -> None:
        """SIGTERM observed after setup cannot allow a first broker receive."""

        waiter = DemucsShutdownWaiter()
        waiter.request_shutdown()
        initial = state()

        result = run_demucs_supervisor_until_stop(
            MagicMock(),
            initial_state=initial,
            database=MagicMock(),
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED)
        self.assertEqual(result.final_state, initial)
        self.assertEqual(result.completed_cycles, 0)
        self.assertIsNone(result.final_cycle)
        run_once.assert_not_called()

    @patch("app.runtime.supervisor_loop.run_one_demucs_supervisor_cycle")
    def test_continue_repeats_once_then_shutdown_result_stops_loop(self, run_once) -> None:
        """Only a completed continue fact permits one additional worker cycle."""

        first = once_result(DemucsSupervisorActionOutcome.CONTINUE, next_state=state(1))
        second = once_result(DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED, next_state=state(2))
        run_once.side_effect = [first, second]
        waiter = DemucsShutdownWaiter()
        channel = MagicMock()
        database = MagicMock()
        source_client = MagicMock()
        artifact_client = MagicMock()
        demucs_runner = MagicMock()

        result = run_demucs_supervisor_until_stop(
            channel,
            initial_state=state(),
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
            demucs_runner=demucs_runner,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
            jitter_fraction=0.25,
        )

        self.assertEqual(result.outcome, DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED)
        self.assertEqual(result.completed_cycles, 2)
        self.assertIs(result.final_cycle, second)
        self.assertEqual(result.final_state, second.next_state)
        self.assertEqual(run_once.call_count, 2)
        self.assertEqual(run_once.call_args_list[1].kwargs["state"], first.next_state)
        self.assertIs(run_once.call_args_list[0].kwargs["demucs_runner"], demucs_runner)

    @patch("app.runtime.supervisor_loop.run_one_demucs_supervisor_cycle")
    def test_signal_after_continue_stops_before_next_worker_cycle(self, run_once) -> None:
        """A SIGTERM during bounded work is observed before another broker poll."""

        waiter = DemucsShutdownWaiter()
        continued = once_result(DemucsSupervisorActionOutcome.CONTINUE, next_state=state(1))

        def continue_then_request_shutdown(*_args, **_kwargs):
            waiter.request_shutdown()
            return continued

        run_once.side_effect = continue_then_request_shutdown
        result = run_demucs_supervisor_until_stop(
            MagicMock(),
            initial_state=state(),
            database=MagicMock(),
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED)
        self.assertEqual(result.completed_cycles, 1)
        self.assertIs(result.final_cycle, continued)
        run_once.assert_called_once()

    @patch("app.runtime.supervisor_loop.run_one_demucs_supervisor_cycle")
    def test_fatal_action_stops_loop_with_terminal_state(self, run_once) -> None:
        """Fatal static configuration never permits another worker cycle."""

        fatal = once_result(DemucsSupervisorActionOutcome.EXIT_FATAL, next_state=state(3))
        run_once.return_value = fatal

        result = run_demucs_supervisor_until_stop(
            MagicMock(),
            initial_state=state(),
            database=MagicMock(),
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=DemucsShutdownWaiter(),
        )

        self.assertEqual(result.outcome, DemucsSupervisorLoopOutcome.EXIT_FATAL)
        self.assertEqual(result.completed_cycles, 1)
        self.assertIs(result.final_cycle, fatal)
        run_once.assert_called_once()

    @patch("app.runtime.supervisor_loop.run_one_demucs_supervisor_cycle")
    def test_runner_failure_propagates_without_another_cycle(self, run_once) -> None:
        """Unclassified errors remain visible to the outer lifecycle owner."""

        failure = RuntimeError("test-only runner failure")
        run_once.side_effect = failure
        with self.assertRaises(RuntimeError) as raised:
            run_demucs_supervisor_until_stop(
                MagicMock(),
                initial_state=state(),
                database=MagicMock(),
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
                shutdown_waiter=DemucsShutdownWaiter(),
            )
        self.assertIs(raised.exception, failure)
        run_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
